import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock
from proxy import app


class MockResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self._text = text
        self._json = json_data or {}
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._text

    def json(self):
        return self._json


# Simple mock client that can be awaited
class MockClient:
    def __init__(self):
        docs_html = (
            "<html><body><p>The free models:</p>"
            "<ul><li>TestFree is a free model</li></ul></body></html>"
        )
        self._docs_response = MockResponse(docs_html)
        self._models_response = MockResponse(
            "", {"data": [{"id": "test-model", "name": "Test Model"}]}
        )

    async def get(self, url, **kw):
        if "docs" in url:
            return self._docs_response
        return self._models_response

    async def post(self, url, **kw):
        return MockResponse(
            "",
            {"type": "message", "role": "assistant",
             "content": [{"type": "text", "text": "ok"}]}
        )

    def build_request(self, method, url, **kw):
        req = MagicMock()
        req.method = method
        req.url = url
        return req

    async def send(self, request, **kw):
        return MockResponse()

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up mock client and provider before each test."""
    from core.state import state
    from providers.registry import set_provider
    from providers.opencode import OpenCodeProvider

    mock_client = MockClient()
    state["client"] = mock_client
    state["routing_cache"] = None
    set_provider(OpenCodeProvider())

    yield

    state["client"] = None
    import providers.registry
    providers.registry._provider = None


@pytest.fixture
async def client(setup_test_env):
    from core.routing import build_routing_table
    from providers.registry import get_provider

    await build_routing_table(get_provider())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac