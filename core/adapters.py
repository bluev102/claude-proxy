import uuid
from typing import Any, Dict, List, Optional

from core.utils import safe_json_loads, text_from_block_like


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
