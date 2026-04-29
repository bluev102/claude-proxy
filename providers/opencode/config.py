"""OpenCode provider — configuration."""
import os

OPENCODE_BASE = os.getenv("OPENCODE_BASE", "https://opencode.ai/zen/v1").rstrip("/")
DOCS_URL = os.getenv("DOCS_URL", "https://opencode.ai/docs/zen/").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "big-pickle").strip()
DOCS_CACHE_TTL = int(os.getenv("DOCS_CACHE_TTL", "900"))
MODELS_CACHE_TTL = int(os.getenv("MODELS_CACHE_TTL", "300"))
