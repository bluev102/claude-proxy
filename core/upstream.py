"""Core upstream — delegates HTTP calls to the active provider."""
from typing import Any, Dict

import httpx

from core.config import REQUEST_TIMEOUT
from core.interfaces import ProviderABC
from core.state import state


async def send_upstream_json(provider: ProviderABC, path: str, payload: Dict[str, Any]) -> httpx.Response:
    client = state.get("client")
    if client is None:
        raise RuntimeError("HTTP client is not initialized")
    return await provider.send_json(
        path, payload, httpx_client=client, timeout=REQUEST_TIMEOUT
    )


async def send_upstream_stream(provider: ProviderABC, path: str, payload: Dict[str, Any]) -> httpx.Response:
    client = state.get("client")
    if client is None:
        raise RuntimeError("HTTP client is not initialized")
    return await provider.send_stream(
        path, payload, httpx_client=client, timeout=REQUEST_TIMEOUT
    )
