from typing import Any, Dict

import httpx

from src.config import OPENCODE_BASE, REQUEST_TIMEOUT


async def get_client():
    from src.config import get_client
    return await get_client()


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
