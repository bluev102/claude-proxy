import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from core.errors import ProxyValidationError


def now_ts() -> float:
    return time.time()


def normalize_model_id(model_id: str) -> str:
    """Strip whitespace from a model ID.

    Provider-specific prefix stripping (e.g. "opencode/") is handled
    by the active provider's ``normalize_model_id`` method.
    """
    return (model_id or "").strip()


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
