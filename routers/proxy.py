import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from core import adapters, routing, transformers, upstream
from core.config import (
    ALLOW_MODEL_FALLBACK,
    FREE_ONLY,
    FREE_FALLBACK_MODEL,
    REQUIRE_LIVE_MODEL,
)
from core.errors import ProxyValidationError, anthropic_error_payload, error_response
from core.normalizers import normalize_anthropic_request
from core.routing import resolve_model_route_with_fallback, resolve_request_model
from core.transformers import (
    build_anthropic_messages_request,
    build_openai_chat_request,
    build_openai_responses_request,
)
from core.upstream import send_upstream_json, send_upstream_stream
from providers.registry import get_provider

router = APIRouter()


@router.get("/healthz")
async def healthz():
    routing_cache = routing.state.get("routing_cache") or {}
    return {
        "ok": True,
        "free_only": FREE_ONLY,
        "require_live_model": REQUIRE_LIVE_MODEL,
        "routable_models": len(routing_cache),
        "free_fallback_model": FREE_FALLBACK_MODEL,
        "allow_model_fallback": ALLOW_MODEL_FALLBACK,
    }


@router.get("/v1/models")
async def list_proxy_models():
    provider = get_provider()
    routes = routing.state.get("routing_cache") or {}
    data = []
    for model_id, meta in sorted(routes.items()):
        data.append({
            "id": model_id,
            "object": "model",
            "owned_by": provider.owned_by(),
            "family": meta.get("family"),
            "endpoint": meta.get("endpoint"),
            "free": meta.get("free", False),
            "live": meta.get("live", False),
        })
    return {"object": "list", "data": data}


@router.get("/debug/catalog")
async def debug_catalog():
    provider = get_provider()
    docs_cat, live_models, routes = await asyncio.gather(
        provider.fetch_catalog(force=True),
        provider.fetch_live_models(force=True),
        routing.build_routing_table(provider, force=True),
    )
    return {
        "free_only": FREE_ONLY,
        "require_live_model": REQUIRE_LIVE_MODEL,
        "free_fallback_model": FREE_FALLBACK_MODEL,
        "allow_model_fallback": ALLOW_MODEL_FALLBACK,
        "docs_routes_count": len(docs_cat["routes"]),
        "docs_free_route_ids_count": len(docs_cat["free_route_ids"]),
        "live_models_count": len(live_models),
        "routing_count": len(routes),
        "pricing_free_names": sorted(list(docs_cat["pricing_free_names"].keys())),
        "bullet_free_names": docs_cat["bullet_free_names"],
        "routes": routes,
    }


async def handle_proxy_request(request: Request):
    from core.config import logger
    from core.utils import ensure_dict
    provider = get_provider()

    try:
        raw_body = ensure_dict(await request.json(), "request body")
    except ProxyValidationError:
        raise
    except Exception as exc:
        return error_response(400, f"Invalid JSON body: {exc}")

    requested_model = resolve_request_model(provider, raw_body)
    model_id, route, used_fallback = await resolve_model_route_with_fallback(provider, requested_model)
    normalized = normalize_anthropic_request(raw_body, model_id)

    logger.info(
        "[PROXY] requested=%s effective=%s fallback=%s family=%s stream=%s provider=%s",
        requested_model, model_id, used_fallback, route["family"], normalized["stream"], provider.provider_name,
    )

    # Route to the appropriate handler based on family
    if route["family"] == "anthropic_messages":
        return await handle_anthropic_messages(provider, route, normalized, raw_body, model_id, requested_model, used_fallback)
    elif route["family"] == "openai_chat":
        return await handle_openai_chat(provider, route, normalized, model_id, requested_model, used_fallback)
    elif route["family"] == "openai_responses":
        return await handle_openai_responses(provider, route, normalized, model_id, requested_model, used_fallback)
    else:
        raise ProxyValidationError(
            422,
            f"Model family `{route['family']}` for `{model_id}` not implemented.",
        )


async def handle_anthropic_messages(provider, route, normalized, raw_body, model_id, requested_model, used_fallback):
    from core.config import logger

    upstream_body = build_anthropic_messages_request(normalized, raw_body)

    if normalized["stream"]:
        resp = await send_upstream_stream(provider, route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        if resp.status_code != 200:
            err = await resp.aread()
            logger.error("[UPSTREAM ERROR] %r", err[:1000])
            return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(err.decode(), "api_error"))
        return stream_response(resp, route["family"], model_id, requested_model, used_fallback)

    resp = await send_upstream_json(provider, route["path"], upstream_body)
    logger.info("[UPSTREAM STATUS] %s", resp.status_code)
    if resp.status_code != 200:
        logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
        return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(resp.text, "api_error"))

    payload = adapters.adapt_anthropic_messages_nonstream(resp.json(), model_id)
    response = JSONResponse(content=payload)
    if used_fallback:
        response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
    return response


async def handle_openai_chat(provider, route, normalized, model_id, requested_model, used_fallback):
    from core.config import logger
    from core.sse import relay_openai_chat_stream_as_anthropic

    upstream_body = build_openai_chat_request(normalized)

    if normalized["stream"]:
        resp = await send_upstream_stream(provider, route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        if resp.status_code != 200:
            err = await resp.aread()
            logger.error("[UPSTREAM ERROR] %r", err[:1000])
            return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(err.decode(), "api_error"))
        return stream_response(resp, route["family"], model_id, requested_model, used_fallback)

    resp = await send_upstream_json(provider, route["path"], upstream_body)
    logger.info("[UPSTREAM STATUS] %s", resp.status_code)
    if resp.status_code != 200:
        logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
        return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(resp.text, "api_error"))

    payload = adapters.adapt_openai_chat_nonstream(resp.json(), model_id)
    response = JSONResponse(content=payload)
    if used_fallback:
        response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
    return response


async def handle_openai_responses(provider, route, normalized, model_id, requested_model, used_fallback):
    from core.config import logger
    from core.sse import relay_openai_responses_stream_as_anthropic

    upstream_body = build_openai_responses_request(normalized)

    if normalized["stream"]:
        resp = await send_upstream_stream(provider, route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        if resp.status_code != 200:
            err = await resp.aread()
            logger.error("[UPSTREAM ERROR] %r", err[:1000])
            return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(err.decode(), "api_error"))
        return stream_response(resp, route["family"], model_id, requested_model, used_fallback)

    resp = await send_upstream_json(provider, route["path"], upstream_body)
    logger.info("[UPSTREAM STATUS] %s", resp.status_code)
    if resp.status_code != 200:
        logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
        return JSONResponse(status_code=resp.status_code, content=anthropic_error_payload(resp.text, "api_error"))

    payload = adapters.adapt_openai_responses_nonstream(resp.json(), model_id)
    response = JSONResponse(content=payload)
    if used_fallback:
        response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
    return response


def stream_response(resp, family, model_id, requested_model, used_fallback):
    from core.sse import relay_openai_chat_stream_as_anthropic, relay_openai_responses_stream_as_anthropic

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if used_fallback:
        headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

    if family == "anthropic_messages":
        return StreamingResponse(resp.aiter_raw(), media_type="text/event-stream", headers=headers)
    elif family == "openai_chat":
        return StreamingResponse(
            relay_openai_chat_stream_as_anthropic(resp, model_id),
            media_type="text/event-stream",
            headers=headers,
        )
    elif family == "openai_responses":
        return StreamingResponse(
            relay_openai_responses_stream_as_anthropic(resp, model_id),
            media_type="text/event-stream",
            headers=headers,
        )
    raise ProxyValidationError(422, f"Unknown family: {family}")


# Register endpoints
router.add_api_route("/v1/messages", handle_proxy_request, methods=["POST"])
router.add_api_route("/v1/messages_beta", handle_proxy_request, methods=["POST"])