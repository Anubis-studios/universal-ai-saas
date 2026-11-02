import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    import httpx

    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
