"""Core runtime state — shared across all requests within one process."""
from typing import Any, Dict

state: Dict[str, Any] = {
    "client": None,  # httpx.AsyncClient, initialized in proxy.py lifespan
    "routing_cache": None,
    "routing_cache_ts": 0.0,
}
