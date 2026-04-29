import json
import uuid
from typing import Any, Dict, List, Optional

from core.config import PASS_THROUGH_ANTHROPIC_EXTRAS
from core.utils import make_object_schema


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
