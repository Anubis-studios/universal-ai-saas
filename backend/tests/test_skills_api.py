import pytest


@pytest.mark.asyncio
async def test_skills_have_ui_metadata(client):
    r = await client.get("/api/v1/skills")
    assert r.status_code == 200
    skills = {s["name"]: s for s in r.json()["skills"]}
    summariser = skills["doc-summarizer"]
    assert summariser["title"] == "Document summariser"
    assert summariser["executable"] is True
    assert [f["name"] for f in summariser["fields"]] == ["text", "max_bullets"]


@pytest.mark.asyncio
async def test_skill_runs_with_mocked_model(client, monkeypatch):
    async def fake_complete(prompt, **kwargs):
        return "• bullet one\n• bullet two"

    gateway = client._transport.app.state.gateway
    monkeypatch.setattr(gateway, "complete", fake_complete)
    r = await client.post(
        "/api/v1/skills/doc-summarizer/run",
        json={"params": {"text": "A very long document. " * 5, "max_bullets": 2}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skill"] == "doc-summarizer"
    assert body["summary"].startswith("• bullet one")
    assert body["word_count"] == 20
