from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.config import PROXY_HOST, PROXY_PORT, REQUEST_TIMEOUT, logger
from core.errors import ProxyValidationError
from providers import load_provider
from providers.registry import set_provider
from routers.proxy import router as proxy_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.state import state
    import httpx

    state["client"] = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    provider = load_provider("opencode")
    set_provider(provider)
    try:
        try:
            from core.routing import build_routing_table
            await build_routing_table(provider, force=True)
            logger.info("[STARTUP] routing bootstrap complete provider=%s", provider.provider_name)
        except Exception as exc:
            logger.warning("[STARTUP] routing bootstrap skipped: %s", exc)
    except Exception:
        state["client"] = None
        raise
    yield
    # cleanup
    client = state.get("client")
    if client is not None:
        await client.aclose()
    state["client"] = None


app = FastAPI(lifespan=lifespan)
app.include_router(proxy_router)


@app.exception_handler(ProxyValidationError)
async def proxy_validation_error_handler(_: Request, exc: ProxyValidationError):
    from core.errors import error_response
    return error_response(exc.status_code, exc.message, exc.error_type)


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)
