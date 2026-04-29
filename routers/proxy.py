import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src import adapters, catalog, errors, routing, transformers, upstream
from src.config import (
    DOCS_URL,
    FREE_ONLY,
    FREE_FALLBACK_MODEL,
    ALLOW_MODEL_FALLBACK,
    REQUIRE_LIVE_MODEL,
    state,
)
from src.errors import ProxyValidationError, anthropic_error_payload, error_response
from src.normalizers import normalize_anthropic_request
from src.routing import resolve_model_route_with_fallback, resolve_request_model
from src.transformers import (
    build_anthropic_messages_request,
    build_openai_chat_request,
    build_openai_responses_request,
)
from src.upstream import send_upstream_json, send_upstream_stream

router = APIRouter()


# =========================================================
# Route handlers
# =========================================================


@router.get("/healthz")
async def healthz():
    routing_cache = state.get("routing_cache") or {}
    docs_catalog = state.get("docs_catalog") or {}
    live_models = state.get("models_cache") or {}

    return {
        "ok": True,
        "free_only": FREE_ONLY,
        "require_live_model": REQUIRE_LIVE_MODEL,
        "docs_url": DOCS_URL,
        "docs_routes_seen": len(docs_catalog.get("routes") or {}),
        "docs_free_route_ids_seen": len(docs_catalog.get("free_route_ids") or []),
        "live_models_seen": len(live_models),
        "routable_models": len(routing_cache),
        "free_fallback_model": FREE_FALLBACK_MODEL,
        "allow_model_fallback": ALLOW_MODEL_FALLBACK,
    }


@router.get("/v1/models")
async def list_proxy_models():
    from src.routing import build_routing_table

    routes = await build_routing_table()
    data = []

    for model_id, meta in sorted(routes.items()):
        data.append(
            {
                "id": model_id,
                "object": "model",
                "owned_by": "opencode-proxy",
                "family": meta.get("family"),
                "endpoint": meta.get("endpoint"),
                "free": meta.get("free", False),
                "live": meta.get("live", False),
            }
        )

    return {"object": "list", "data": data}


@router.get("/debug/catalog")
async def debug_catalog():
    from src.routing import build_routing_table

    docs_cat, live_models, routes = await asyncio.gather(
        catalog.fetch_docs_catalog(force=True),
        catalog.fetch_live_models(force=True),
        build_routing_table(force=True),
    )

    return {
        "docs_url": DOCS_URL,
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


@router.post("/v1/messages")
@router.post("/v1/messages_beta")
async def proxy_messages(request: Request):
    from src.config import logger
    from src.utils import ensure_dict

    try:
        raw_body = ensure_dict(await request.json(), "request body")
    except ProxyValidationError:
        raise
    except Exception as exc:
        return error_response(400, f"Invalid JSON body: {exc}")

    requested_model = resolve_request_model(raw_body)
    model_id, route, used_fallback = await resolve_model_route_with_fallback(requested_model)
    normalized = normalize_anthropic_request(raw_body, model_id)

    logger.info(
        "[PROXY] requested_model=%s effective_model=%s fallback=%s family=%s stream=%s msg_count=%s tool_count=%s",
        requested_model,
        model_id,
        used_fallback,
        route["family"],
        normalized["stream"],
        len(normalized["messages"]),
        len(normalized.get("tools", [])),
    )

    if route["family"] == "anthropic_messages":
        upstream_body = build_anthropic_messages_request(normalized, raw_body)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            return StreamingResponse(
                resp.aiter_raw(),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapters.adapt_anthropic_messages_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    if route["family"] == "openai_chat":
        upstream_body = build_openai_chat_request(normalized)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            from src.sse import relay_openai_chat_stream_as_anthropic
            return StreamingResponse(
                relay_openai_chat_stream_as_anthropic(resp, model_id),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapters.adapt_openai_chat_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    if route["family"] == "openai_responses":
        upstream_body = build_openai_responses_request(normalized)

        if normalized["stream"]:
            resp = await send_upstream_stream(route["path"], upstream_body)
            logger.info("[UPSTREAM STATUS] %s", resp.status_code)
            logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

            if resp.status_code != 200:
                err = await resp.aread()
                logger.error("[UPSTREAM ERROR] %r", err[:1000])
                return JSONResponse(
                    status_code=resp.status_code,
                    content=anthropic_error_payload(err.decode("utf-8", errors="replace"), "api_error"),
                )

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            if used_fallback:
                headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"

            from src.sse import relay_openai_responses_stream_as_anthropic
            return StreamingResponse(
                relay_openai_responses_stream_as_anthropic(resp, model_id),
                media_type="text/event-stream",
                headers=headers,
            )

        resp = await send_upstream_json(route["path"], upstream_body)
        logger.info("[UPSTREAM STATUS] %s", resp.status_code)
        logger.info("[UPSTREAM CONTENT-TYPE] %s", resp.headers.get("content-type"))

        if resp.status_code != 200:
            logger.error("[UPSTREAM ERROR] %s", resp.text[:1000])
            return JSONResponse(
                status_code=resp.status_code,
                content=anthropic_error_payload(resp.text, "api_error"),
            )

        payload = adapters.adapt_openai_responses_nonstream(resp.json(), model_id)
        response = JSONResponse(content=payload)
        if used_fallback:
            response.headers["X-Proxy-Model-Fallback"] = f"{requested_model}->{model_id}"
        return response

    raise ProxyValidationError(
        422,
        (
            f"Model family `{route['family']}` for `{model_id}` is discovered from docs, "
            "but this proxy does not yet implement a safe Anthropic->Google adapter."
        ),
    )
