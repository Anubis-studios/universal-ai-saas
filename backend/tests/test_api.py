import pytest
import respx
from httpx import Response


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_models_catalogue(client):
    r = await client.get("/api/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 15
    names = {m["name"] for m in body["models"]}
    assert {"llama-3.3-70b", "gemini-2.0-flash", "flux-schnell"} <= names
    groq = next(m for m in body["models"] if m["name"] == "llama-3.3-70b")
    assert groq["available"] is True  # test key is set


@pytest.mark.asyncio
@respx.mock
async def test_chat_endpoint(client):
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "42"}}], "usage": {"total_tokens": 3},
        })
    )
    r = await client.post("/api/v1/chat", json={"prompt": "meaning of life?", "max_tokens": 20})
    assert r.status_code == 200
    assert r.json()["text"] == "42"


@pytest.mark.asyncio
async def test_chat_requires_input(client):
    r = await client.post("/api/v1/chat", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_skill_validation(client):
    r = await client.post("/api/v1/skills/doc-summarizer/run", json={"params": {"text": "short"}})
    assert r.status_code == 400
    assert "40 characters" in r.json()["error"]


@pytest.mark.asyncio
async def test_unknown_skill(client):
    r = await client.post("/api/v1/skills/nope/run", json={"params": {}})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_key_gate(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PLATFORM_API_KEY", "s3cret")
    assert (await client.get("/api/v1/models")).status_code == 401
    assert (await client.get("/api/v1/models", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/api/v1/models", headers={"Authorization": "Bearer s3cret"})).status_code == 200
    # health stays open for the platform's health checks
    assert (await client.get("/health")).status_code == 200
