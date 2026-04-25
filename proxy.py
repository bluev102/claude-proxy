import json
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from dotenv import load_dotenv
import os
import uvicorn
import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

OPENCODE_BASE = os.getenv("OPENCODE_BASE", "https://opencode.ai/zen/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "minimax-m2.5-free")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")

client = httpx.AsyncClient(timeout=300.0)


def extract_text(blocks):
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return str(blocks)
    return " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def anthropic_to_upstream(anthropic_body: dict) -> dict:
    body = dict(anthropic_body)

    if not body.get("model"):
        body["model"] = MODEL_NAME
    else:
        body["model"] = MODEL_NAME

    logger.debug(f"[REQUEST IN] body keys: {list(anthropic_body.keys())}")
    logger.debug(f"[REQUEST OUT] model={body.get('model')} stream={body.get('stream')}")

    return body


async def relay_sse(resp: httpx.Response):
    try:
        async for chunk in resp.aiter_raw():
            if chunk:
                logger.debug(f"[DOWNSTREAM RAW] {chunk[:300]!r}")
                yield chunk
    finally:
        await resp.aclose()


@app.post("/v1/messages")
@app.post("/v1/messages_beta")
async def proxy_messages(request: Request):
    anthropic_body = await request.json()
    stream = anthropic_body.get("stream", False)

    logger.info(
        f"[PROXY] stream={stream}, max_tokens={anthropic_body.get('max_tokens')}, "
        f"thinking={'thinking' in anthropic_body}"
    )

    upstream_body = anthropic_to_upstream(anthropic_body)
    url = f"{OPENCODE_BASE}/messages"
    headers = {"Content-Type": "application/json"}

    if stream:
        req = client.build_request(
            "POST",
            url,
            json=upstream_body,
            headers=headers,
            timeout=300.0,
        )
        resp = await client.send(req, stream=True)

        logger.info(f"[UPSTREAM STATUS] {resp.status_code}")
        logger.info(f"[UPSTREAM CONTENT-TYPE] {resp.headers.get('content-type')}")

        if resp.status_code != 200:
            err = await resp.aread()
            logger.error(f"[UPSTREAM ERROR] {err[:1000]!r}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=err.decode("utf-8", errors="replace"),
            )

        return StreamingResponse(
            relay_sse(resp),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    resp = await client.post(url, json=upstream_body, headers=headers)
    logger.info(f"[UPSTREAM STATUS] {resp.status_code}")

    if resp.status_code != 200:
        logger.error(f"[UPSTREAM ERROR] {resp.text[:1000]}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()
    logger.debug(f"[UPSTREAM JSON] {json.dumps(data, ensure_ascii=False)[:1500]}")

    if (
        isinstance(data, dict)
        and data.get("type") == "message"
        and data.get("role") == "assistant"
        and "content" in data
    ):
        return JSONResponse(content=data)

    content = ""
    if "content" in data:
        content = extract_text(data["content"])
    elif "choices" in data and data["choices"]:
        content = data["choices"][0].get("message", {}).get("content", "")
    else:
        content = data.get("text", "")

    usage = data.get("usage", {})
    anthropic_resp = {
        "id": data.get("id", "msg_1"),
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": data.get("model", MODEL_NAME),
        "stop_reason": data.get("stop_reason", "end_turn"),
        "stop_sequence": data.get("stop_sequence"),
        "usage": {
            "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
        },
    }
    return JSONResponse(content=anthropic_resp)


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)