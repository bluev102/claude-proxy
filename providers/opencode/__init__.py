# Trigger @register_provider("opencode") in provider.py, and re-export for tests
from providers.opencode import provider  # noqa: F401, E402
from providers.opencode.provider import OpenCodeProvider  # noqa: F401, E402
