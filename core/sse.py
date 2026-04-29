import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from core.errors import ProxyValidationError
from core.utils import safe_json_loads


def sse_encode(event: str, payload: Dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def iter_sse_events(resp: httpx.Response) -> AsyncGenerator[Dict[str, Any], None]:
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
