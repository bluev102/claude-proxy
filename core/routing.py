"""Core routing — builds the routing table from a provider's catalog."""
import asyncio
from typing import Any, Dict, Tuple

from core.config import (
    ALLOW_MODEL_FALLBACK,
    FREE_ONLY,
    REQUIRE_LIVE_MODEL,
    ROUTING_CACHE_TTL,
)
from core.state import state
from core.errors import ProxyValidationError
from core.interfaces import ProviderABC
from core.utils import now_ts


async def build_routing_table(provider: ProviderABC, force: bool = False) -> Dict[str, Dict[str, Any]]:
    cached = state.get("routing_cache")
    cached_ts = state.get("routing_cache_ts", 0.0)

    if not force and cached and (now_ts() - cached_ts) < ROUTING_CACHE_TTL:
        return cached

    docs_catalog, live_models = await asyncio.gather(
        provider.fetch_catalog(force=force),
        provider.fetch_live_models(force=force),
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

    from core.config import logger
    logger.info(
        "[ROUTING] built table total=%s free_only=%s require_live=%s provider=%s",
        len(final_routes),
        FREE_ONLY,
        REQUIRE_LIVE_MODEL,
        provider.provider_name,
    )

    return final_routes


def resolve_request_model(provider: ProviderABC, body: Dict[str, Any]) -> str:
    model = provider.normalize_model_id(str(body.get("model") or provider.default_model or ""))
    if not model:
        raise ProxyValidationError(422, "Model is empty after resolution")
    return model


async def resolve_model_route(provider: ProviderABC, model_id: str) -> Dict[str, Dict[str, Any]]:
    routes = await build_routing_table(provider)
    route = routes.get(model_id)
    if route:
        return route

    docs_catalog, live_models = await asyncio.gather(
        provider.fetch_catalog(),
        provider.fetch_live_models(),
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


async def resolve_model_route_with_fallback(
    provider: ProviderABC, requested_model_id: str
) -> Tuple[str, Dict[str, Any], bool]:
    routes = await build_routing_table(provider)

    if requested_model_id in routes:
        return requested_model_id, routes[requested_model_id], False

    if ALLOW_MODEL_FALLBACK and FREE_ONLY:
        fallback_id = provider.normalize_model_id(provider.default_fallback_model)
        fallback_route = routes.get(fallback_id)
        if fallback_route:
            from core.config import logger
            logger.warning(
                "[MODEL FALLBACK] requested=%s -> fallback=%s provider=%s",
                requested_model_id,
                fallback_id,
                provider.provider_name,
            )
            return fallback_id, fallback_route, True

    route = await resolve_model_route(provider, requested_model_id)
    return requested_model_id, route, False
