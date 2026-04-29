import logging
import os
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("opencode-proxy")

# =========================================================
# Config
# =========================================================

DOCS_URL = os.getenv("DOCS_URL", "https://opencode.ai/docs/zen/").strip()
OPENCODE_BASE = os.getenv("OPENCODE_BASE", "https://opencode.ai/zen/v1").rstrip("/")

DEFAULT_MODEL = os.getenv("MODEL_NAME", "big-pickle").strip()

FREE_ONLY = os.getenv("FREE_ONLY", "true").lower() == "true"
REQUIRE_LIVE_MODEL = os.getenv("REQUIRE_LIVE_MODEL", "true").lower() == "true"

FREE_FALLBACK_MODEL = os.getenv("FREE_FALLBACK_MODEL", "big-pickle").strip()
ALLOW_MODEL_FALLBACK = os.getenv("ALLOW_MODEL_FALLBACK", "true").lower() == "true"

DOCS_CACHE_TTL = int(os.getenv("DOCS_CACHE_TTL", "900"))
MODELS_CACHE_TTL = int(os.getenv("MODELS_CACHE_TTL", "300"))
ROUTING_CACHE_TTL = int(os.getenv("ROUTING_CACHE_TTL", "300"))

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))
CATALOG_TIMEOUT = float(os.getenv("CATALOG_TIMEOUT", "30"))

PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))

PASS_THROUGH_ANTHROPIC_EXTRAS = {
    x.strip()
    for x in os.getenv("PASS_THROUGH_ANTHROPIC_EXTRAS", "metadata").split(",")
    if x.strip()
}

# =========================================================
# Runtime state
# =========================================================



async def get_client() -> httpx.AsyncClient:
    client = state.get("client")
    if client is None:
        raise RuntimeError("HTTP client is not initialized")
    return client


state: Dict[str, Any] = {
    "client": None,
    "docs_html": None,
    "docs_html_ts": 0.0,
    "docs_catalog": None,
    "docs_catalog_ts": 0.0,
    "models_cache": None,
    "models_cache_ts": 0.0,
    "routing_cache": None,
    "routing_cache_ts": 0.0,
}
