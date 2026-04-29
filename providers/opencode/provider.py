"""OpenCode provider — implements ProviderABC."""
from typing import Any, Dict

import httpx

from core.config import FREE_FALLBACK_MODEL
from core.interfaces import Catalog, ProviderABC
from providers import register_provider
from providers.opencode import catalog as opencode_catalog
from providers.opencode import config as opencode_config
from providers.opencode import upstream as opencode_upstream


@register_provider("opencode")
class OpenCodeProvider(ProviderABC):
    _catalog_cache: opencode_catalog.OpenCodeCatalogCache | None = None

    def _cache(self) -> opencode_catalog.OpenCodeCatalogCache:
        if self._catalog_cache is None:
            self._catalog_cache = opencode_catalog.OpenCodeCatalogCache()
        return self._catalog_cache

    @property
    def provider_name(self) -> str:
        return "opencode"

    @property
    def base_url(self) -> str:
        return opencode_config.OPENCODE_BASE

    @property
    def default_model(self) -> str:
        return opencode_config.MODEL_NAME

    @property
    def default_fallback_model(self) -> str:
        return FREE_FALLBACK_MODEL or opencode_config.MODEL_NAME

    def normalize_model_id(self, raw: str) -> str:
        """Strip 'opencode/' prefix and whitespace from a model ID."""
        value = (raw or "").strip()
        if value.startswith("opencode/"):
            value = value.split("/", 1)[1].strip()
        return value

    async def fetch_catalog(self, *, force: bool = False) -> Catalog:
        cache = self._cache()
        result = await opencode_catalog.fetch_docs_catalog(cache, force=force)
        # Cast to Catalog — opencode_catalog returns the right shape
        return result  # type: ignore[return-value]

    async def fetch_live_models(
        self, *, force: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        cache = self._cache()
        return await opencode_catalog.fetch_live_models(cache, force=force)

    async def send_json(
        self, path: str, payload: Dict[str, Any], *, httpx_client: httpx.AsyncClient, timeout: float
    ) -> httpx.Response:
        return await opencode_upstream.send_upstream_json(
            path, payload, httpx_client=httpx_client, timeout=timeout
        )

    async def send_stream(
        self, path: str, payload: Dict[str, Any], *, httpx_client: httpx.AsyncClient, timeout: float
    ) -> httpx.Response:
        return await opencode_upstream.send_upstream_stream(
            path, payload, httpx_client=httpx_client, timeout=timeout
        )

    def owned_by(self) -> str:
        return "opencode-proxy"
