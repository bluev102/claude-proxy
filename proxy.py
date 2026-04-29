from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from routers.proxy import router as proxy_router
from src.config import PROXY_HOST, PROXY_PORT, REQUEST_TIMEOUT, get_client, logger
from src.errors import ProxyValidationError
from src.routing import build_routing_table


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.config import state
    import httpx

    state["client"] = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        try:
            await build_routing_table(force=True)
        except Exception as exc:
            logger.warning("[STARTUP] routing bootstrap skipped: %s", exc)
        yield
    finally:
        client = state.get("client")
        if client is not None:
            await client.aclose()
        state["client"] = None


app = FastAPI(lifespan=lifespan)
app.include_router(proxy_router)


@app.exception_handler(ProxyValidationError)
async def proxy_validation_error_handler(_: Request, exc: ProxyValidationError):
    from src.errors import error_response
    return error_response(exc.status_code, exc.message, exc.error_type)


if __name__ == "__main__":
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT)
