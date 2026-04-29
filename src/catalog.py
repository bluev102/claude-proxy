import asyncio
from typing import Any, Dict

from src.config import (
    CATALOG_TIMEOUT,
    DOCS_CACHE_TTL,
    DOCS_URL,
    MODELS_CACHE_TTL,
    OPENCODE_BASE,
    state,
)
from src.parsing import parse_docs_catalog_from_html
from src.utils import normalize_model_id, now_ts


async def get_client():
    from src.config import get_client
    return await get_client()


async def fetch_docs_html(force: bool = False) -> str:
    cached = state.get("docs_html")
    cached_ts = state.get("docs_html_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < DOCS_CACHE_TTL:
        return cached

    client = await get_client()
    resp = await client.get(DOCS_URL, timeout=CATALOG_TIMEOUT)
    resp.raise_for_status()

    html = resp.text
    state["docs_html"] = html
    state["docs_html_ts"] = now_ts()

    from src.config import logger
    logger.info("[DOCS] refreshed from %s", DOCS_URL)
    return html


async def fetch_docs_catalog(force: bool = False) -> Dict[str, Any]:
    cached = state.get("docs_catalog")
    cached_ts = state.get("docs_catalog_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < DOCS_CACHE_TTL:
        return cached

    html = await fetch_docs_html(force=force)
    catalog = parse_docs_catalog_from_html(html)

    state["docs_catalog"] = catalog
    state["docs_catalog_ts"] = now_ts()

    from src.config import logger
    logger.info(
        "[DOCS PARSE] routes=%s free_pricing_names=%s bullet_free_names=%s free_route_ids=%s",
        len(catalog["routes"]),
        len(catalog["pricing_free_names"]),
        len(catalog["bullet_free_names"]),
        len(catalog["free_route_ids"]),
    )

    return catalog


async def fetch_live_models(force: bool = False) -> Dict[str, Dict[str, Any]]:
    cached = state.get("models_cache")
    cached_ts = state.get("models_cache_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < MODELS_CACHE_TTL:
        return cached

    client = await get_client()
    url = f"{OPENCODE_BASE}/models"
    resp = await client.get(url, timeout=CATALOG_TIMEOUT)
    resp.raise_for_status()

    payload = resp.json()
    models: Dict[str, Dict[str, Any]] = {}

    for item in payload.get("data", []):
        if isinstance(item, dict) and item.get("id"):
            model_id = normalize_model_id(str(item["id"]))
            models[model_id] = item

    state["models_cache"] = models
    state["models_cache_ts"] = now_ts()

    from src.config import logger
    logger.info("[LIVE MODELS] refreshed, total=%s", len(models))
    return models
