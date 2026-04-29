import asyncio
from typing import Any, Dict, Tuple

from src.catalog import fetch_docs_catalog, fetch_live_models
from src.config import (
    ALLOW_MODEL_FALLBACK,
    FREE_FALLBACK_MODEL,
    FREE_ONLY,
    REQUIRE_LIVE_MODEL,
    ROUTING_CACHE_TTL,
    state,
)
from src.errors import ProxyValidationError
from src.utils import now_ts, normalize_model_id


async def build_routing_table(force: bool = False) -> Dict[str, Dict[str, Any]]:
    cached = state.get("routing_cache")
    cached_ts = state.get("routing_cache_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < ROUTING_CACHE_TTL:
        return cached

    docs_catalog, live_models = await asyncio.gather(
        fetch_docs_catalog(force=force),
        fetch_live_models(force=force),
    )

    routes = docs_catalog["routes"]
    free_route_ids = docs_catalog["free_route_ids"]

    final_routes: Dict[str, Dict[str, Any]] = {}

    for model_id, meta in routes.items():
        if FREE_ONLY and model_id not in free_route_ids:
            continue
        if REQUIRE_LIVE_MODEL and model_id not in live_models:
            continue

        merged = dict(meta)
        merged["free"] = model_id in free_route_ids
        merged["live"] = model_id in live_models
        merged["live_meta"] = live_models.get(model_id)
        final_routes[model_id] = merged

    state["routing_cache"] = final_routes
    state["routing_cache_ts"] = now_ts()

    from src.config import logger
    logger.info(
        "[ROUTING] built table total=%s free_only=%s require_live=%s fallback=%s allow_fallback=%s",
        len(final_routes),
        FREE_ONLY,
        REQUIRE_LIVE_MODEL,
        FREE_FALLBACK_MODEL,
        ALLOW_MODEL_FALLBACK,
    )

    return final_routes


def resolve_request_model(body: Dict[str, Any]) -> str:
    from src.config import DEFAULT_MODEL
    model = normalize_model_id(str(body.get("model") or DEFAULT_MODEL))
    if not model:
        raise ProxyValidationError(422, "Model is empty after resolution")
    return model


async def resolve_model_route(model_id: str) -> Dict[str, Dict[str, Any]]:
    routes = await build_routing_table()
    route = routes.get(model_id)
    if route:
        return route

    docs_catalog, live_models = await asyncio.gather(
        fetch_docs_catalog(),
        fetch_live_models(),
    )

    docs_routes = docs_catalog["routes"]
    free_route_ids = docs_catalog["free_route_ids"]

    in_live = model_id in live_models
    in_docs_routes = model_id in docs_routes
    is_docs_free = model_id in free_route_ids

    if in_live and in_docs_routes and FREE_ONLY and not is_docs_free:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in live catalog and docs routing, "
                "but docs do not mark it as free for this proxy."
            ),
        )

    if in_live and not in_docs_routes:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in live catalog, "
                "but routing metadata could not be derived from docs."
            ),
        )

    if in_docs_routes and REQUIRE_LIVE_MODEL and not in_live:
        raise ProxyValidationError(
            422,
            (
                f"Model `{model_id}` exists in docs routing, "
                "but is not currently present in the live /v1/models catalog."
            ),
        )

    raise ProxyValidationError(
        400,
        f"Unknown or unavailable model `{model_id}` for this proxy.",
    )


async def resolve_model_route_with_fallback(requested_model_id: str) -> Tuple[str, Dict[str, Any], bool]:
    routes = await build_routing_table()

    if requested_model_id in routes:
        return requested_model_id, routes[requested_model_id], False

    if ALLOW_MODEL_FALLBACK and FREE_ONLY:
        fallback_id = normalize_model_id(FREE_FALLBACK_MODEL)
        fallback_route = routes.get(fallback_id)
        if fallback_route:
            from src.config import logger
            logger.warning(
                "[MODEL FALLBACK] requested=%s -> fallback=%s",
                requested_model_id,
                fallback_id,
            )
            return fallback_id, fallback_route, True

    route = await resolve_model_route(requested_model_id)
    return requested_model_id, route, False
