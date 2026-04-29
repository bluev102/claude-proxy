import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock
from proxy import app


class MockResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self._text = text
        self._json = json_data or {}
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._text

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def mock_http_client():
    from src import config

    mock_client = MagicMock()

    docs_html = """
    <html><body>
    <p>The free models:</p>
    <ul><li>TestFree is a free model</li></ul>
    </body></html>
    """
    models_json = {"data": [{"id": "test-model", "name": "Test Model"}]}

    mock_get = MagicMock()
    mock_get_doget = AsyncMock(return_value=MockResponse(docs_html))
    mock_get_models = AsyncMock(return_value=MockResponse("", models_json))
    mock_client.get = MagicMock(side_effect=lambda url, **kw: mock_get_doget() if "docs" in url else mock_get_models())
    mock_client.post = AsyncMock()
    mock_client.build_request = MagicMock()
    mock_client.send = AsyncMock()
    mock_client.aclose = AsyncMock()

    old_client = config.state.get("client")
    config.state["client"] = mock_client

    yield mock_client

    config.state["client"] = old_client


@pytest.fixture
async def client(mock_http_client):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
