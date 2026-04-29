"""OpenCode provider — catalog fetching and caching."""
from typing import Any, Dict

import httpx

from providers.opencode import config as opencode_config
from providers.opencode import parsing as opencode_parsing


class OpenCodeCatalogCache:
    """Cache stored on the provider instance (not shared with core)."""

    def __init__(self):
        self.docs_html: str | None = None
        self.docs_html_ts: float = 0.0
        self.docs_catalog: Dict[str, Any] | None = None
        self.docs_catalog_ts: float = 0.0
        self.models_cache: Dict[str, Dict[str, Any]] | None = None
        self.models_cache_ts: float = 0.0


def _client() -> httpx.AsyncClient:
    from core.state import state
    c = state.get("client")
    if c is None:
        raise RuntimeError("HTTP client is not initialized")
    return c


async def fetch_docs_html(cache: OpenCodeCatalogCache, force: bool = False) -> str:
    from core.utils import now_ts

    if not force and cache.docs_html and (now_ts() - cache.docs_html_ts) < opencode_config.DOCS_CACHE_TTL:
        return cache.docs_html

    client = _client()
    resp = await client.get(opencode_config.DOCS_URL, timeout=30.0)
    resp.raise_for_status()

    html = resp.text
    cache.docs_html = html
    cache.docs_html_ts = now_ts()

    from core.config import logger
    logger.info("[OPENCODE] docs refreshed from %s", opencode_config.DOCS_URL)
    return html


async def fetch_docs_catalog(cache: OpenCodeCatalogCache, force: bool = False) -> Dict[str, Any]:
    from core.utils import now_ts

    if not force and cache.docs_catalog and (now_ts() - cache.docs_catalog_ts) < opencode_config.DOCS_CACHE_TTL:
        return cache.docs_catalog

    html = await fetch_docs_html(cache, force=force)
    catalog = opencode_parsing.parse_docs_catalog_from_html(html)

    cache.docs_catalog = catalog
    cache.docs_catalog_ts = now_ts()

    from core.config import logger
    logger.info(
        "[OPENCODE PARSE] routes=%s free_pricing_names=%s bullet_free_names=%s free_route_ids=%s",
        len(catalog["routes"]),
        len(catalog["pricing_free_names"]),
        len(catalog["bullet_free_names"]),
        len(catalog["free_route_ids"]),
    )
    return catalog


async def fetch_live_models(cache: OpenCodeCatalogCache, force: bool = False) -> Dict[str, Dict[str, Any]]:
    from core.utils import now_ts

    if not force and cache.models_cache and (now_ts() - cache.models_cache_ts) < opencode_config.MODELS_CACHE_TTL:
        return cache.models_cache

    client = _client()
    url = f"{opencode_config.OPENCODE_BASE}/models"
    resp = await client.get(url, timeout=30.0)
    resp.raise_for_status()

    payload = resp.json()
    models: Dict[str, Dict[str, Any]] = {}

    for item in payload.get("data", []):
        if isinstance(item, dict) and item.get("id"):
            # Strip "opencode/" prefix from live model IDs
            raw_id = str(item["id"])
            model_id = opencode_parsing.normalize_model_id_from_str(raw_id)
            models[model_id] = item

    cache.models_cache = models
    cache.models_cache_ts = now_ts()

    from core.config import logger
    logger.info("[OPENCODE LIVE MODELS] refreshed, total=%s", len(models))
    return models
