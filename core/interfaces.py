"""Provider interface — the contract every provider must implement."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Set, Tuple, TypedDict

import httpx


class ModelMeta(TypedDict):
    model_id: str
    model_name: str
    name_key: str
    family: str
    path: str
    endpoint: str
    sdk_package: str
    free: bool
    live: bool
    live_meta: Dict[str, Any] | None


class Catalog(TypedDict):
    routes: Dict[str, ModelMeta]
    free_route_ids: Set[str]
    pricing_free_names: Dict[str, Any]
    bullet_free_names: List[str]
    free_name_keys: Set[str]


class ProviderABC(ABC):
    """Abstract base for all AI provider adapters.

    Core never imports provider-specific code. It calls only these methods
    and properties. Each provider lives under ``providers/<name>/``.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier used in logs and error messages."""
        ...

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL for all upstream API calls (no trailing slash)."""
        ...

    @property
    def default_model(self) -> str:
        """Default model when client sends none."""
        return ""

    @property
    def default_fallback_model(self) -> str:
        """Fallback model when FREE_ONLY=true and requested model is unavailable."""
        return ""

    @abstractmethod
    def normalize_model_id(self, raw: str) -> str:
        """Strip provider-specific prefixes (e.g. 'opencode/') from a model ID."""
        ...

    @abstractmethod
    async def fetch_catalog(self, *, force: bool = False) -> Catalog:
        """Fetch and parse the provider's model catalog from docs/schemas."""
        ...

    @abstractmethod
    async def fetch_live_models(
        self, *, force: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch the live /v1/models list from the provider's API.

        Returns a dict keyed by model_id (bare, not prefixed).
        """
        ...

    @abstractmethod
    async def send_json(
        self, path: str, payload: Dict[str, Any], *, httpx_client: httpx.AsyncClient, timeout: float
    ) -> httpx.Response:
        """Send a JSON POST request to the upstream API."""
        ...

    @abstractmethod
    async def send_stream(
        self, path: str, payload: Dict[str, Any], *, httpx_client: httpx.AsyncClient, timeout: float
    ) -> httpx.Response:
        """Send a streaming POST request to the upstream API.

        The returned response must have ``stream=True`` on the underlying request.
        """
        ...

    @abstractmethod
    def owned_by(self) -> str:
        """Value for the ``owned_by`` field in /v1/models responses."""
        ...
