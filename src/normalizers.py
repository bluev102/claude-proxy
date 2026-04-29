import uuid
from typing import Any, Dict, List, Optional

from src.errors import ProxyValidationError
from src.utils import clamp_number, make_object_schema, normalize_system_to_text


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
            from src.config import logger
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
