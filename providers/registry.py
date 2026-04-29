"""Global registry for provider access in routers."""
from core.interfaces import ProviderABC

# Provider instance — set by proxy.py lifespan
_provider: ProviderABC | None = None


def get_provider() -> ProviderABC:
    if _provider is None:
        raise RuntimeError("Provider not initialized")
    return _provider


def set_provider(p: ProviderABC):
    global _provider
    _provider = p