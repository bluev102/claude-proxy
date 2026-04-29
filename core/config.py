"""Provider-neutral configuration — env vars that apply regardless of which provider is loaded."""
import logging
import os
from typing import Set

from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("claude-proxy")

# ── Provider selection ──────────────────────────────────────────
PROVIDER_NAME = os.getenv("PROVIDER_NAME", "opencode").strip()

# ── Proxy server ──────────────────────────────────────────────
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))

# ── Routing policy ────────────────────────────────────────────
FREE_ONLY = os.getenv("FREE_ONLY", "true").lower() == "true"
REQUIRE_LIVE_MODEL = os.getenv("REQUIRE_LIVE_MODEL", "true").lower() == "true"
ALLOW_MODEL_FALLBACK = os.getenv("ALLOW_MODEL_FALLBACK", "true").lower() == "true"
FREE_FALLBACK_MODEL = os.getenv("FREE_FALLBACK_MODEL", "").strip()

# ── Cache TTLs ────────────────────────────────────────────────
ROUTING_CACHE_TTL = int(os.getenv("ROUTING_CACHE_TTL", "300"))

# ── Timeouts ──────────────────────────────────────────────────
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))
CATALOG_TIMEOUT = float(os.getenv("CATALOG_TIMEOUT", "30"))

# ── Passthrough ───────────────────────────────────────────────
PASS_THROUGH_ANTHROPIC_EXTRAS: Set[str] = {
    x.strip()
    for x in os.getenv("PASS_THROUGH_ANTHROPIC_EXTRAS", "metadata").split(",")
    if x.strip()
}
