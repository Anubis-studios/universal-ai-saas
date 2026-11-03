import pytest


@pytest.mark.asyncio
async def test_skills_have_ui_metadata(client):
    r = await client.get("/api/v1/skills", params={"q": "document summariser", "limit": 100})
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


@pytest.mark.asyncio
async def test_catalogue_is_searchable(client):
    body = (await client.get("/api/v1/skills", params={"q": "kubernetes"})).json()
    assert body["total"] > 10
    assert body["mode"].startswith("fts")
    names = [s["name"] for s in body["skills"]]
    assert any("kubernetes" in n for n in names)
    assert all(s["executable"] for s in body["skills"])


@pytest.mark.asyncio
async def test_pagination_walks_the_whole_catalogue(client):
    first = (await client.get("/api/v1/skills", params={"limit": 100})).json()
    second = (await client.get("/api/v1/skills", params={"limit": 100, "offset": 100})).json()
    assert first["total"] == second["total"] >= 2000
    assert first["count"] == 100
    assert not {s["name"] for s in first["skills"]} & {s["name"] for s in second["skills"]}


@pytest.mark.asyncio
async def test_categories_and_detail(client):
    cats = (await client.get("/api/v1/skills/categories")).json()
    assert cats["count"] > 50
    assert cats["categories"]["security"] > 0
    detail = (await client.get("/api/v1/skills/kubernetes-architect")).json()
    assert detail["category"] == "devops"
    assert detail["body"]
    assert (await client.get("/api/v1/skills/no-such-skill-xyz")).status_code == 404


@pytest.mark.asyncio
async def test_catalogue_skill_executes(client, monkeypatch):
    async def fake_complete(prompt, **kwargs):
        assert "SKILL INSTRUCTIONS" in prompt
        return "Report: done"

    monkeypatch.setattr(client._transport.app.state.gateway, "complete", fake_complete)
    r = await client.post(
        "/api/v1/skills/kubernetes-architect/run", json={"params": {"input": "design a cluster"}}
    )
    assert r.status_code == 200
    assert r.json()["tier"] == "catalogue"
    assert r.json()["result"] == "Report: done"


@pytest.mark.asyncio
async def test_unknown_skill_and_missing_input_are_400(client):
    assert (await client.post("/api/v1/skills/nope-xyz/run", json={"params": {}})).status_code == 400
    r = await client.post("/api/v1/skills/kubernetes-architect/run", json={"params": {}})
    assert r.status_code == 400
