import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
import uvicorn
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# =========================================================
# Bootstrap
# =========================================================

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("opencode-proxy")

# =========================================================
# Config
# =========================================================

DOCS_URL = os.getenv("DOCS_URL", "https://opencode.ai/docs/zen/").strip()
OPENCODE_BASE = os.getenv("OPENCODE_BASE", "https://opencode.ai/zen/v1").rstrip("/")

DEFAULT_MODEL = os.getenv("MODEL_NAME", "big-pickle").strip()

FREE_ONLY = os.getenv("FREE_ONLY", "true").lower() == "true"
REQUIRE_LIVE_MODEL = os.getenv("REQUIRE_LIVE_MODEL", "true").lower() == "true"

# Nếu client gửi model paid / không hợp lệ nhưng proxy đang FREE_ONLY=true,
# tự fallback sang model free này nếu nó đang routable.
FREE_FALLBACK_MODEL = os.getenv("FREE_FALLBACK_MODEL", "big-pickle").strip()
ALLOW_MODEL_FALLBACK = os.getenv("ALLOW_MODEL_FALLBACK", "true").lower() == "true"

DOCS_CACHE_TTL = int(os.getenv("DOCS_CACHE_TTL", "900"))
MODELS_CACHE_TTL = int(os.getenv("MODELS_CACHE_TTL", "300"))
ROUTING_CACHE_TTL = int(os.getenv("ROUTING_CACHE_TTL", "300"))

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))
CATALOG_TIMEOUT = float(os.getenv("CATALOG_TIMEOUT", "30"))

PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))

PASS_THROUGH_ANTHROPIC_EXTRAS = {
    x.strip()
    for x in os.getenv("PASS_THROUGH_ANTHROPIC_EXTRAS", "metadata").split(",")
    if x.strip()
}

# =========================================================
# Runtime state
# =========================================================

state: Dict[str, Any] = {
    "client": None,
    "docs_html": None,
    "docs_html_ts": 0.0,
    "docs_catalog": None,
    "docs_catalog_ts": 0.0,
    "models_cache": None,
    "models_cache_ts": 0.0,
    "routing_cache": None,
    "routing_cache_ts": 0.0,
}

# =========================================================
# Error helpers
# =========================================================


class ProxyValidationError(Exception):
    def __init__(self, status_code: int, message: str, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


def anthropic_error_payload(message: str, error_type: str = "invalid_request_error") -> Dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def error_response(status_code: int, message: str, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=anthropic_error_payload(message, error_type=error_type),
    )


# =========================================================
# Lifespan / HTTP client
# =========================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["client"] = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        try:
            await build_routing_table(force=True)
        except Exception as exc:
            logger.warning("[STARTUP] routing bootstrap skipped: %s", exc)
        yield
    finally:
        client = state.get("client")
        if client is not None:
            await client.aclose()
        state["client"] = None


app = FastAPI(lifespan=lifespan)


async def get_client() -> httpx.AsyncClient:
    client = state.get("client")
    if client is None:
        raise RuntimeError("HTTP client is not initialized")
    return client


# =========================================================
# Utility helpers
# =========================================================


def now_ts() -> float:
    return time.time()


def normalize_model_id(model_id: str) -> str:
    value = (model_id or "").strip()
    if value.startswith("opencode/"):
        value = value.split("/", 1)[1].strip()
    return value


def normalize_label(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_name_key(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\bfree\b", " ", text)
    text = re.sub(r"\bflash\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ensure_dict(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ProxyValidationError(400, f"`{name}` must be a JSON object")
    return value


def clamp_number(value: Any, name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProxyValidationError(422, f"`{name}` must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except Exception as exc:
        raise ProxyValidationError(422, f"`{name}` must be a number") from exc


def safe_json_loads(value: str) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return None


def text_from_block_like(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text", "") or ""))
        return "".join(parts)
    if isinstance(value, dict):
        if value.get("type") in {"text", "input_text", "output_text"}:
            return str(value.get("text", "") or "")
    return str(value)


def normalize_system_to_text(system_value: Any) -> Optional[str]:
    if system_value is None:
        return None
    if isinstance(system_value, str):
        text = system_value.strip()
        return text if text else None
    if isinstance(system_value, list):
        parts: List[str] = []
        for item in system_value:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
            elif isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        out = "\n".join(parts).strip()
        return out or None
    text = str(system_value).strip()
    return text or None


def make_object_schema(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.setdefault("type", "object")
    if not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    if "required" in out and not isinstance(out["required"], list):
        out.pop("required", None)
    return out


# =========================================================
# HTML docs parsing
# =========================================================


def html_table_to_dicts(table) -> List[Dict[str, str]]:
    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    headers = [normalize_label(cell.get_text(" ", strip=True)) for cell in header_cells]

    out: List[Dict[str, str]] = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        values = [cell.get_text(" ", strip=True) for cell in cells]
        if len(values) < len(headers):
            values += [""] * (len(headers) - len(values))
        elif len(values) > len(headers):
            values = values[:len(headers)]

        out.append({headers[i]: values[i] for i in range(len(headers))})

    return out


def parse_docs_catalog_from_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    routes: Dict[str, Dict[str, Any]] = {}
    pricing_free_names: Dict[str, Dict[str, Any]] = {}
    bullet_free_names: List[str] = []

    for table in tables:
        entries = html_table_to_dicts(table)
        if not entries:
            continue

        headers = set(entries[0].keys())

        if {"model", "model id", "endpoint", "ai sdk package"}.issubset(headers):
            for entry in entries:
                model_name = entry.get("model", "")
                model_id = normalize_model_id(entry.get("model id", ""))
                endpoint = entry.get("endpoint", "")
                sdk_package = entry.get("ai sdk package", "")

                if not model_id or not endpoint:
                    continue

                family = None
                path = None

                if endpoint.endswith("/v1/messages"):
                    family = "anthropic_messages"
                    path = "/messages"
                elif endpoint.endswith("/v1/chat/completions"):
                    family = "openai_chat"
                    path = "/chat/completions"
                elif endpoint.endswith("/v1/responses"):
                    family = "openai_responses"
                    path = "/responses"
                elif "/v1/models/" in endpoint:
                    family = "google_models"
                    path = endpoint.split("/zen/v1", 1)[-1]

                if family:
                    routes[model_id] = {
                        "model_name": model_name,
                        "name_key": normalize_name_key(model_name),
                        "model_id": model_id,
                        "endpoint": endpoint,
                        "sdk_package": sdk_package,
                        "family": family,
                        "path": path,
                    }

        if {"model", "input", "output", "cached read", "cached write"}.issubset(headers):
            for entry in entries:
                model_name = entry.get("model", "")
                input_price = normalize_label(entry.get("input", ""))
                output_price = normalize_label(entry.get("output", ""))
                cached_read = normalize_label(entry.get("cached read", ""))

                if input_price == "free" and output_price == "free" and cached_read == "free":
                    pricing_free_names[model_name] = {
                        "model_name": model_name,
                        "name_key": normalize_name_key(model_name),
                        "input_price": entry.get("input", ""),
                        "output_price": entry.get("output", ""),
                        "cached_read_price": entry.get("cached read", ""),
                        "cached_write_price": entry.get("cached write", ""),
                    }

    free_anchor = soup.find(string=re.compile(r"The free models:", re.I))
    if free_anchor:
        parent = free_anchor.parent
        next_ul = parent.find_next("ul") if parent else None
        if next_ul:
            for li in next_ul.find_all("li"):
                text = li.get_text(" ", strip=True)
                name = re.split(r"\s+is\s+", text, maxsplit=1)[0].strip()
                if name:
                    bullet_free_names.append(name)

    free_name_keys = {meta["name_key"] for meta in pricing_free_names.values()}
    free_name_keys.update(normalize_name_key(name) for name in bullet_free_names if name)

    free_route_ids = {
        model_id
        for model_id, meta in routes.items()
        if meta["name_key"] in free_name_keys
    }

    return {
        "routes": routes,
        "pricing_free_names": pricing_free_names,
        "bullet_free_names": bullet_free_names,
        "free_name_keys": free_name_keys,
        "free_route_ids": free_route_ids,
    }


# =========================================================
# Catalog loading / caching
# =========================================================


async def fetch_docs_html(force: bool = False) -> str:
    cached = state.get("docs_html")
    cached_ts = state.get("docs_html_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < DOCS_CACHE_TTL:
        return cached

    client = await get_client()
    resp = await client.get(DOCS_URL, timeout=CATALOG_TIMEOUT)
    resp.raise_for_status()

    html = resp.text
    state["docs_html"] = html
    state["docs_html_ts"] = now_ts()

    logger.info("[DOCS] refreshed from %s", DOCS_URL)
    return html


async def fetch_docs_catalog(force: bool = False) -> Dict[str, Any]:
    cached = state.get("docs_catalog")
    cached_ts = state.get("docs_catalog_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < DOCS_CACHE_TTL:
        return cached

    html = await fetch_docs_html(force=force)
    catalog = parse_docs_catalog_from_html(html)

    state["docs_catalog"] = catalog
    state["docs_catalog_ts"] = now_ts()

    logger.info(
        "[DOCS PARSE] routes=%s free_pricing_names=%s bullet_free_names=%s free_route_ids=%s",
        len(catalog["routes"]),
        len(catalog["pricing_free_names"]),
        len(catalog["bullet_free_names"]),
        len(catalog["free_route_ids"]),
    )

    return catalog


async def fetch_live_models(force: bool = False) -> Dict[str, Dict[str, Any]]:
    cached = state.get("models_cache")
    cached_ts = state.get("models_cache_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < MODELS_CACHE_TTL:
        return cached

    client = await get_client()
    url = f"{OPENCODE_BASE}/models"
    resp = await client.get(url, timeout=CATALOG_TIMEOUT)
    resp.raise_for_status()

    payload = resp.json()
    models: Dict[str, Dict[str, Any]] = {}

    for item in payload.get("data", []):
        if isinstance(item, dict) and item.get("id"):
            model_id = normalize_model_id(str(item["id"]))
            models[model_id] = item

    state["models_cache"] = models
    state["models_cache_ts"] = now_ts()

    logger.info("[LIVE MODELS] refreshed, total=%s", len(models))
    return models


async def build_routing_table(force: bool = False) -> Dict[str, Dict[str, Any]]:
    cached = state.get("routing_cache")
    cached_ts = state.get("routing_cache_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < ROUTING_CACHE_TTL:
        return cached

    docs_catalog, live_models = await asyncio.gather(
        fetch_docs_catalog(force=force),
        fetch_live_models(force=force),
    )

    routes = docs_catalog["routes"]
    free_route_ids = docs_catalog["free_route_ids"]

    final_routes: Dict[str, Dict[str, Any]] = {}

    for model_id, meta in routes.items():
        if FREE_ONLY and model_id not in free_route_ids:
            continue
        if REQUIRE_LIVE_MODEL and model_id not in live_models:
            continue

        merged = dict(meta)
        merged["free"] = model_id in free_route_ids
        merged["live"] = model_id in live_models
        merged["live_meta"] = live_models.get(model_id)
        final_routes[model_id] = merged

    state["routing_cache"] = final_routes
    state["routing_cache_ts"] = now_ts()

    logger.info(
        "[ROUTING] built table total=%s free_only=%s require_live=%s fallback=%s allow_fallback=%s",
        len(final_routes),
        FREE_ONLY,
        REQUIRE_LIVE_MODEL,
        FREE_FALLBACK_MODEL,
        ALLOW_MODEL_FALLBACK,
    )

    return final_routes


# =========================================================
# Model routing diagnostics
# =========================================================


def resolve_request_model(body: Dict[str, Any]) -> str:
    model = normalize_model_id(str(body.get("model") or DEFAULT_MODEL))
    if not model:
        raise ProxyValidationError(422, "Model is empty after resolution")
    return model


async def resolve_model_route(model_id: str) -> Dict[str, Any]:
    routes = await build_routing_table()
    route = routes.get(model_id)
    if route:
        return route

    docs_catalog, live_models = await asyncio.gather(
        fetch_docs_catalog(),
        fetch_live_models(),
    )

    docs_routes = docs_catalog["routes"]
    free_route_ids = docs_catalog["free_route_ids"]

    in_live = model_id in live_models
    in_docs_routes = model_id in docs_routes
    is_docs_free = model_id in free_route_ids

    if in_live and in_docs_routes and FREE_ONLY and not is_docs_free:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in live catalog and docs routing, "
                "but docs do not mark it as free for this proxy."
            ),
        )

    if in_live and not in_docs_routes:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in live catalog, "
                "but routing metadata could not be derived from docs."
            ),
        )

    if in_docs_routes and REQUIRE_LIVE_MODEL and not in_live:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in docs routing, "
                "but is not currently present in the live /v1/models catalog."
            ),
        )

    raise ProxyValidationError(
        400,
        f"Unknown or unavailable model `{model_id}` for this proxy.",
    )


async def resolve_model_route_with_fallback(requested_model_id: str) -> Tuple[str, Dict[str, Any], bool]:
    routes = await build_routing_table()

    if requested_model_id in routes:
        return requested_model_id, routes[requested_model_id], False

    if ALLOW_MODEL_FALLBACK and FREE_ONLY:
        fallback_id = normalize_model_id(FREE_FALLBACK_MODEL)
        fallback_route = routes.get(fallback_id)
        if fallback_route:
            logger.warning(
                "[MODEL FALLBACK] requested=%s -> fallback=%s",
                requested_model_id,
                fallback_id,
            )
            return fallback_id, fallback_route, True

    route = await resolve_model_route(requested_model_id)
    return requested_model_id, route, False


# =========================================================
# Anthropic request normalization
# =========================================================


def normalize_anthropic_messages(messages: Any) -> List[Dict[str, Any]]:
    if not isinstance(messages, list):
        raise ProxyValidationError(422, "`messages` must be an array")

    out: List[Dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ProxyValidationError(422, f"`messages[{idx}]` must be an object")

        role = msg.get("role")
        if role not in {"user", "assistant"}:
            raise ProxyValidationError(422, f"`messages[{idx}].role` must be `user` or `assistant`")

        content = msg.get("content", "")

        if isinstance(content, str):
            text = content.strip()
            blocks = [{"type": "text", "text": text}] if text else []

        elif isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, str):
                    text = block.strip()
                    if text:
                        blocks.append({"type": "text", "text": text})
                    continue

                if not isinstance(block, dict):
                    continue

                btype = block.get("type")

                if btype == "text":
                    text = str(block.get("text", "") or "")
                    if text:
                        blocks.append({"type": "text", "text": text})

                elif btype == "tool_use":
                    name = str(block.get("name", "")).strip()
                    if not name:
                        continue
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                            "name": name,
                            "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                        }
                    )

                elif btype == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if not tool_use_id:
                        continue

                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        normalized_parts = []
                        for item in result_content:
                            if isinstance(item, str):
                                normalized_parts.append({"type": "text", "text": item})
                            elif isinstance(item, dict) and item.get("type") == "text":
                                normalized_parts.append(
                                    {"type": "text", "text": str(item.get("text", "") or "")}
                                )
                        result_content = normalized_parts
                    elif not isinstance(result_content, str):
                        result_content = str(result_content or "")

                    block_out = {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_content,
                    }
                    if "is_error" in block:
                        block_out["is_error"] = bool(block.get("is_error"))
                    blocks.append(block_out)

        else:
            text = str(content or "").strip()
            blocks = [{"type": "text", "text": text}] if text else []

        if not blocks:
            raise ProxyValidationError(422, f"`messages[{idx}]` becomes empty after normalization")

        out.append({"role": role, "content": blocks})

    if not out:
        raise ProxyValidationError(422, "No valid messages found")

    return out


def normalize_anthropic_tools(tools: Any) -> List[Dict[str, Any]]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise ProxyValidationError(422, "`tools` must be an array")

    out: List[Dict[str, Any]] = []
    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue

        name = str(tool.get("name", "")).strip()
        if not name:
            logger.warning("[TOOLS] dropping tools[%s] because name is empty", idx)
            continue

        out.append(
            {
                "name": name,
                "description": str(tool.get("description", "") or ""),
                "input_schema": make_object_schema(tool.get("input_schema")),
            }
        )
    return out


def normalize_anthropic_request(body: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    messages = normalize_anthropic_messages(body.get("messages"))
    tools = normalize_anthropic_tools(body.get("tools"))
    stream = bool(body.get("stream", False))

    normalized: Dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": stream,
    }

    system_text = normalize_system_to_text(body.get("system"))
    if system_text:
        normalized["system"] = system_text

    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        max_tokens_num = int(clamp_number(max_tokens, "max_tokens"))
        if max_tokens_num <= 0:
            raise ProxyValidationError(422, "`max_tokens` must be > 0")
        normalized["max_tokens"] = max_tokens_num

    temperature = body.get("temperature")
    if temperature is not None:
        normalized["temperature"] = clamp_number(temperature, "temperature")

    if tools:
        normalized["tools"] = tools

    return normalized


# =========================================================
# Transformer: Anthropic -> Anthropic/messages upstream
# =========================================================


def build_anthropic_messages_request(normalized: Dict[str, Any], raw_body: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": normalized["model"],
        "messages": normalized["messages"],
        "stream": normalized["stream"],
    }

    if "system" in normalized:
        body["system"] = normalized["system"]
    if "max_tokens" in normalized:
        body["max_tokens"] = normalized["max_tokens"]
    if "temperature" in normalized:
        body["temperature"] = normalized["temperature"]
    if "tools" in normalized:
        body["tools"] = normalized["tools"]

    for key in PASS_THROUGH_ANTHROPIC_EXTRAS:
        if key in raw_body:
            body[key] = raw_body[key]

    return body


# =========================================================
# Transformer: Anthropic -> OpenAI chat/completions
# =========================================================


def anthropic_tools_to_openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for tool in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": make_object_schema(tool.get("input_schema")),
                },
            }
        )
    return out


def extract_tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "") or ""))
        return "".join(parts)
    return str(content or "")


def anthropic_messages_to_openai_chat_messages(
    messages: List[Dict[str, Any]],
    system_text: Optional[str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if system_text:
        out.append({"role": "system", "content": system_text})

    for msg in messages:
        role = msg["role"]
        blocks = msg["content"]

        if role == "assistant":
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []

            for block in blocks:
                if block["type"] == "text":
                    text_parts.append(str(block.get("text", "") or ""))
                elif block["type"] == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        }
                    )

            if text_parts or tool_calls:
                item: Dict[str, Any] = {"role": "assistant"}
                item["content"] = "".join(text_parts) if text_parts else None
                if tool_calls:
                    item["tool_calls"] = tool_calls
                out.append(item)

        elif role == "user":
            pending_text: List[str] = []

            def flush_user_text():
                if pending_text:
                    out.append({"role": "user", "content": "".join(pending_text)})
                    pending_text.clear()

            for block in blocks:
                if block["type"] == "text":
                    pending_text.append(str(block.get("text", "") or ""))
                elif block["type"] == "tool_result":
                    flush_user_text()
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": extract_tool_result_text(block.get("content", "")),
                        }
                    )

            flush_user_text()

    return out


def build_openai_chat_request(normalized: Dict[str, Any]) -> Dict[str, Any]:
    messages = anthropic_messages_to_openai_chat_messages(
        normalized["messages"],
        normalized.get("system"),
    )

    body: Dict[str, Any] = {
        "model": normalized["model"],
        "messages": messages,
        "stream": normalized["stream"],
    }

    if "max_tokens" in normalized:
        body["max_tokens"] = normalized["max_tokens"]
    if "temperature" in normalized:
        body["temperature"] = normalized["temperature"]
    if normalized.get("tools"):
        body["tools"] = anthropic_tools_to_openai_tools(normalized["tools"])

    if normalized["stream"]:
        body["stream_options"] = {"include_usage": True}

    return body


# =========================================================
# Transformer: Anthropic -> OpenAI responses
# =========================================================


def anthropic_messages_to_responses_input(
    messages: List[Dict[str, Any]],
    system_text: Optional[str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if system_text:
        out.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_text}],
            }
        )

    for msg in messages:
        role = msg["role"]
        text_parts: List[str] = []

        for block in msg["content"]:
            if block["type"] == "text":
                text_parts.append(str(block.get("text", "") or ""))
            elif block["type"] == "tool_result":
                text_parts.append(extract_tool_result_text(block.get("content", "")))

        out.append(
            {
                "role": role,
                "content": [{"type": "input_text", "text": "".join(text_parts)}],
            }
        )

    return out


def build_openai_responses_request(normalized: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": normalized["model"],
        "input": anthropic_messages_to_responses_input(
            normalized["messages"],
            normalized.get("system"),
        ),
        "stream": normalized["stream"],
    }

    if "max_tokens" in normalized:
        body["max_output_tokens"] = normalized["max_tokens"]
    if "temperature" in normalized:
        body["temperature"] = normalized["temperature"]

    if normalized.get("tools"):
        body["tools"] = [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": make_object_schema(tool.get("input_schema")),
            }
            for tool in normalized["tools"]
        ]

    return body


# =========================================================
# Response adapters: non-stream
# =========================================================


def build_anthropic_message_response(
    content_blocks: List[Dict[str, Any]],
    model_id: str,
    upstream_id: Optional[str] = None,
    stop_reason: str = "end_turn",
    usage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usage = usage or {}
    return {
        "id": upstream_id or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model_id,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
        },
    }


def adapt_anthropic_messages_nonstream(data: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    if (
        isinstance(data, dict)
        and data.get("type") == "message"
        and data.get("role") == "assistant"
        and isinstance(data.get("content"), list)
    ):
        return data

    text = ""
    if "content" in data:
        text = text_from_block_like(data["content"])
    elif "text" in data:
        text = str(data.get("text", "") or "")

    return build_anthropic_message_response(
        content_blocks=[{"type": "text", "text": text}],
        model_id=data.get("model", model_id),
        upstream_id=data.get("id"),
        stop_reason=data.get("stop_reason", "end_turn"),
        usage=data.get("usage", {}),
    )


def adapt_openai_chat_nonstream(data: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    choices = data.get("choices") or []
    message = (choices[0] or {}).get("message", {}) if choices else {}

    content_blocks: List[Dict[str, Any]] = []

    content = message.get("content")
    if isinstance(content, str) and content:
        content_blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        text = text_from_block_like(content)
        if text:
            content_blocks.append({"type": "text", "text": text})

    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        args_raw = function.get("arguments", "{}")
        args_obj = safe_json_loads(args_raw)
        if not isinstance(args_obj, dict):
            args_obj = {"raw": args_raw}

        content_blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "name": function.get("name", ""),
                "input": args_obj,
            }
        )

    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]

    stop_reason = "end_turn"
    finish_reason = (choices[0] or {}).get("finish_reason") if choices else None
    if finish_reason == "tool_calls":
        stop_reason = "tool_use"

    return build_anthropic_message_response(
        content_blocks=content_blocks,
        model_id=data.get("model", model_id),
        upstream_id=data.get("id"),
        stop_reason=stop_reason,
        usage=data.get("usage", {}),
    )


def extract_responses_output_text(data: Dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: List[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                parts.append(str(content.get("text", "") or ""))
    return "".join(parts)


def adapt_openai_responses_nonstream(data: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    text = extract_responses_output_text(data)
    return build_anthropic_message_response(
        content_blocks=[{"type": "text", "text": text}],
        model_id=data.get("model", model_id),
        upstream_id=data.get("id"),
        stop_reason="end_turn",
        usage=data.get("usage", {}),
    )


# =========================================================
# SSE helpers
# =========================================================


def sse_encode(event: str, payload: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def iter_sse_events(resp: httpx.Response):
    event_name = None
    data_lines: List[str] = []

    async for line in resp.aiter_lines():
        if line == "":
            if data_lines:
                yield {"event": event_name, "data": "\n".join(data_lines)}
            event_name = None
            data_lines = []
            continue

        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())

    if data_lines:
        yield {"event": event_name, "data": "\n".join(data_lines)}


# =========================================================
# Streaming adapter: OpenAI chat -> Anthropic SSE
# =========================================================


async def relay_openai_chat_stream_as_anthropic(resp: httpx.Response, model_id: str):
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    usage: Dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

    message_started = False
    text_block_started = False
    text_block_index = 0
    stop_reason = "end_turn"

    tool_state: Dict[int, Dict[str, Any]] = {}
    next_block_index = 1

    def maybe_start_message() -> Optional[bytes]:
        nonlocal message_started
        if message_started:
            return None
        message_started = True
        return sse_encode(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model_id,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": usage,
                },
            },
        )

    try:
        async for sse in iter_sse_events(resp):
            raw = sse["data"]
            if raw == "[DONE]":
                break

            data = safe_json_loads(raw)
            if not isinstance(data, dict):
                continue

            starter = maybe_start_message()
            if starter is not None:
                yield starter

            if isinstance(data.get("usage"), dict):
                usage.update(data["usage"])

            choices = data.get("choices") or []
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta") or {}

            finish_reason = choice.get("finish_reason")
            if finish_reason == "tool_calls":
                stop_reason = "tool_use"
            elif finish_reason:
                stop_reason = "end_turn"

            text_delta = delta.get("content")
            if isinstance(text_delta, str) and text_delta:
                if not text_block_started:
                    text_block_started = True
                    yield sse_encode(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": text_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )

                yield sse_encode(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_block_index,
                        "delta": {"type": "text_delta", "text": text_delta},
                    },
                )

            for tc in delta.get("tool_calls") or []:
                tc_index = tc.get("index", 0)
                fn = tc.get("function") or {}

                if tc_index not in tool_state:
                    tool_state[tc_index] = {
                        "anthropic_index": next_block_index,
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                        "name": "",
                        "started": False,
                    }
                    next_block_index += 1

                tstate = tool_state[tc_index]

                if fn.get("name"):
                    tstate["name"] = fn["name"]

                if not tstate["started"] and tstate["name"]:
                    tstate["started"] = True
                    yield sse_encode(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": tstate["anthropic_index"],
                            "content_block": {
                                "type": "tool_use",
                                "id": tstate["id"],
                                "name": tstate["name"],
                                "input": {},
                            },
                        },
                    )

                arguments_delta = fn.get("arguments")
                if tstate["started"] and isinstance(arguments_delta, str) and arguments_delta:
                    yield sse_encode(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": tstate["anthropic_index"],
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": arguments_delta,
                            },
                        },
                    )

        if message_started:
            if text_block_started:
                yield sse_encode("content_block_stop", {"type": "content_block_stop", "index": text_block_index})

            for _, tstate in sorted(tool_state.items(), key=lambda x: x[0]):
                if tstate["started"]:
                    yield sse_encode(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": tstate["anthropic_index"]},
                    )

            yield sse_encode(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": usage,
                },
            )
            yield sse_encode("message_stop", {"type": "message_stop"})

    finally:
        await resp.aclose()


# =========================================================
# Streaming adapter: OpenAI responses -> Anthropic SSE
# =========================================================


async def relay_openai_responses_stream_as_anthropic(resp: httpx.Response, model_id: str):
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    usage: Dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

    message_started = False
    text_block_started = False
    text_block_index = 0

    def maybe_start_message() -> Optional[bytes]:
        nonlocal message_started
        if message_started:
            return None
        message_started = True
        return sse_encode(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model_id,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": usage,
                },
            },
        )

    try:
        async for sse in iter_sse_events(resp):
            raw = sse["data"]
            if raw == "[DONE]":
                break

            data = safe_json_loads(raw)
            if not isinstance(data, dict):
                continue

            if isinstance(data.get("usage"), dict):
                usage.update(data["usage"])

            dtype = data.get("type")

            if dtype == "response.output_text.delta":
                starter = maybe_start_message()
                if starter is not None:
                    yield starter

                if not text_block_started:
                    text_block_started = True
                    yield sse_encode(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": text_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )

                delta_text = data.get("delta") or data.get("text") or ""
                if delta_text:
                    yield sse_encode(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": text_block_index,
                            "delta": {"type": "text_delta", "text": delta_text},
                        },
                    )

            elif dtype == "response.error":
                raise ProxyValidationError(
                    502,
                    str(data.get("error") or "Upstream responses stream error"),
                    "api_error",
                )

            elif dtype == "response.completed":
                break

        if message_started:
            if text_block_started:
                yield sse_encode("content_block_stop", {"type": "content_block_stop", "index": text_block_index})

            yield sse_encode(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": usage,
                },
            )
            yield sse_encode("message_stop", {"type": "message_stop"})

    finally:
        await resp.aclose()


# =========================================================
# Upstream request helpers
# =========================================================


async def send_upstream_json(path: str, payload: Dict[str, Any]) -> httpx.Response:
    client = await get_client()
    return await client.post(
        f"{OPENCODE_BASE}{path}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )


async def send_upstream_stream(path: str, payload: Dict[str, Any]) -> httpx.Response:
    client = await get_client()
    req = client.build_request(
        "POST",
        f"{OPENCODE_BASE}{path}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    return await client.send(req, stream=True)


# =========================================================
# Route handlers
# =========================================================


@app.exception_handler(ProxyValidationError)
async def proxy_validation_error_handler(_: Request, exc: ProxyValidationError):
    return error_response(exc.status_code, exc.message, exc.error_type)


@app.get("/healthz")
async def healthz():
    routing = state.get("routing_cache") or {}
    docs_catalog = state.get("docs_catalog") or {}
    live_models = state.get("models_cache") or {}

    return {
        "ok": True,
        "free_only": FREE_ONLY,
        "require_live_model": REQUIRE_LIVE_MODEL,
        "docs_url": DOCS_URL,
        "docs_routes_seen": len(docs_catalog.get("routes") or {}),
        "docs_free_route_ids_seen": len(docs_catalog.get("free_route_ids") or []),
        "live_models_seen": len(live_models),
        "routable_models": len(routing),
        "free_fallback_model": FREE_FALLBACK_MODEL,
        "allow_model_fallback": ALLOW_MODEL_FALLBACK,
    }


@app.get("/v1/models")
async def list_proxy_models():
    routes = await build_routing_table()
    data = []

    for model_id, meta in sorted(routes.items()):
        data.append(
            {
                "id": model_id,
                "object": "model",
                "owned_by": "opencode-proxy",
                "family": meta.get("family"),
                "endpoint": meta.get("endpoint"),
                "free": meta.get("free", False),
                "live": meta.get("live", False),
            }
        )

    return {"object": "list", "data": data}


@app.get("/debug/catalog")
async def debug_catalog():
    docs_catalog, live_models, routes = await asyncio.gather(
        fetch_docs_catalog(force=True),
        fetch_live_models(force=True),
        build_routing_table(force=True),
    )

    return {
        "docs_url": DOCS_URL,
        "free_only": FREE_ONLY,
        "require_live_model": REQUIRE_LIVE_MODEL,
        "free_fallback_model": FREE_FALLBACK_MODEL,
        "allow_model_fallback": ALLOW_MODEL_FALLBACK,
        "docs_routes_count": len(docs_catalog["routes"]),
        "docs_free_route_ids_count": len(docs_catalog["free_route_ids"]),
        "live_models_count": len(live_models),
        "routing_count": len(routes),
        "pricing_free_names": sorted(list(docs_catalog["pricing_free_names"].keys())),
        "bullet_free_names": docs_catalog["bullet_free_names"],
        "routes": routes,
    }


@app.post("/v1/messages")
@app.post("/v1/messages_beta")
async def proxy_messages(request: Request):
    try:
        raw_body = ensure_dict(await request.json(), "request body")
    except ProxyValidationError:
        raise
    except Exception as exc:
        return error_response(400, f"Invalid JSON body: {exc}")

    requested_model = resolve_request_model(raw_body)
    model_id, route, used_fallback = await resolve_model_route_with_fallback(requested_model)
    normalized = normalize_anthropic_request(raw_body, model_id)

    logger.info(
        "[PROXY] requested_model=%s effective_model=%s fallback=%s family=%s stream=%s msg_count=%s tool_count=%s",
        requested_model,
        model_id,
        used_fallback,
        route["family"],
        normalized["stream"],
        len(normalized["messages"]),
        len(normalized.get("tools", [])),
    )

    if route["family"] == "anthropic_messages":
        upstream_body = build_anthropic_messages_request(normalized, raw_body)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            return StreamingResponse(
                resp.aiter_raw(),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapt_anthropic_messages_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    if route["family"] == "openai_chat":
        upstream_body = build_openai_chat_request(normalized)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            return StreamingResponse(
                relay_openai_chat_stream_as_anthropic(resp, model_id),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapt_openai_chat_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    if route["family"] == "openai_responses":
        upstream_body = build_openai_responses_request(normalized)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            return StreamingResponse(
                relay_openai_responses_stream_as_anthropic(resp, model_id),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapt_openai_responses_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    raise ProxyValidationError(
        422,
        (
            f"Model family `{route['family']}` for `{model_id}` is discovered from docs, "
            "but this one-file proxy does not yet implement a safe Anthropic->Google adapter."
        ),
    )


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)