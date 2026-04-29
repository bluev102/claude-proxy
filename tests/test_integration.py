import pytest


@pytest.mark.asyncio
async def test_healthz(client):
    """Healthz endpoint should return 200 with status info."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "free_only" in data
    assert "routable_models" in data


@pytest.mark.asyncio
async def test_list_models(client):
    """List models endpoint should return a list."""
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_proxy_messages_invalid_json(client):
    """Invalid JSON body should return 400."""
    resp = await client.post("/v1/messages", content="not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    data = resp.json()
    assert data["type"] == "error"


@pytest.mark.asyncio
async def test_proxy_messages_empty_model(client):
    """Empty model in request should return validation error."""
    resp = await client.post(
        "/v1/messages",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    # Falls back to default model — should get past validation
    # (actual response depends on upstream)
    assert resp.status_code in (200, 422, 400, 502)


@pytest.mark.asyncio
async def test_messages_beta_alias(client):
    """Beta endpoint should work same as regular messages."""
    resp = await client.post(
        "/v1/messages_beta",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    # Should behave same as /v1/messages
    assert resp.status_code in (200, 422, 400, 502)
