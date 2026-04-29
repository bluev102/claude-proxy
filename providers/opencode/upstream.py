"""OpenCode provider — HTTP client for upstream API calls."""
from typing import Any, Dict

import httpx

from providers.opencode import config as opencode_config


async def send_upstream_json(
    path: str,
    payload: Dict[str, Any],
    *,
    httpx_client: httpx.AsyncClient,
    timeout: float,
) -> httpx.Response:
    return await httpx_client.post(
        f"{opencode_config.OPENCODE_BASE}{path}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )


async def send_upstream_stream(
    path: str,
    payload: Dict[str, Any],
    *,
    httpx_client: httpx.AsyncClient,
    timeout: float,
) -> httpx.Response:
    req = httpx_client.build_request(
        "POST",
        f"{opencode_config.OPENCODE_BASE}{path}",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    return await httpx_client.send(req, stream=True)
