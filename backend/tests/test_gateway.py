import pytest
import respx
from httpx import Response

from app.services.universal_gateway import UNIVERSAL_REGISTRY, UniversalGateway


@pytest.mark.asyncio
async def test_registry_entries_are_consistent():
    for name, d in UNIVERSAL_REGISTRY.items():
        assert d.name == name
        assert d.category in {"text", "image", "video", "audio", "embedding"}
        assert d.model_id


@pytest.mark.asyncio
@respx.mock
async def test_groq_chat_returns_text_and_usage():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "Hello from Llama."}}],
            "usage": {"total_tokens": 12},
        })
    )
    gw = UniversalGateway()
    try:
        result = await gw.chat([{"role": "user", "content": "hi"}], "llama-3.3-70b")
        assert result["text"] == "Hello from Llama."
        assert result["usage"]["total_tokens"] == 12
        assert result["provider"] == "groq"
    finally:
        await gw.close()


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_falls_through_to_next_provider():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(429, json={"error": {"message": "rate limited"}})
    )
    respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
        return_value=Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "Gemini here."}]}}],
            "usageMetadata": {"totalTokenCount": 8},
        })
    )
    gw = UniversalGateway()
    try:
        result = await gw.chat([{"role": "user", "content": "hi"}], "auto")
        assert result["provider"] == "google"
        assert result["text"] == "Gemini here."
    finally:
        await gw.close()


@pytest.mark.asyncio
async def test_unknown_model_raises_404():
    from app.core.errors import ModelNotFound

    gw = UniversalGateway()
    try:
        with pytest.raises(ModelNotFound):
            await gw.chat([{"role": "user", "content": "hi"}], "gpt-9")
    finally:
        await gw.close()


@pytest.mark.asyncio
@respx.mock
async def test_streaming_yields_deltas():
    body = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    gw = UniversalGateway()
    try:
        events = [e async for e in gw.stream_chat([{"role": "user", "content": "hi"}], "llama-3.3-70b")]
        text = "".join(e.get("text", "") for e in events if e["event"] == "delta")
        assert text == "Hello"
        assert events[-1]["event"] == "done"
    finally:
        await gw.close()
