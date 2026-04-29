"""Provider registry — loads provider by name at startup."""
from typing import Dict, Type

PROVIDER_REGISTRY: Dict[str, Type] = {}


def register_provider(name: str):
    """Decorator that registers a provider class in the global registry."""

    def deco(cls: Type) -> Type:
        PROVIDER_REGISTRY[name] = cls
        return cls

    return deco


def load_provider(name: str):
    """Return a new instance of the provider registered under `name`."""
    # Auto-import all known providers so their @register_provider decorators run.
    import providers.opencode  # noqa: F401

    if name not in PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Available: {list(PROVIDER_REGISTRY.keys())}"
        )
    return PROVIDER_REGISTRY[name]()
