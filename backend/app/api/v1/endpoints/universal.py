from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.errors import GatewayError
from app.models.schemas import (
    DiscoverResponse,
    GameRequest,
    ModelInfo,
    RunRequest,
    RunResponse,
    SkillRequest,
    SkillsResponse,
    VideoRequest,
)
from app.services.agentic_skills import AgenticSkillsService
from app.services.game_forge import GAMES_DIR, GameForge
from app.services.library_db import LibraryDB
from app.services.universal_gateway import UniversalGateway
from app.services.video_engine import VideoEngine

router = APIRouter()


def gateway(request: Request) -> UniversalGateway:
    return request.app.state.gateway


def skills(request: Request) -> AgenticSkillsService:
    return request.app.state.skills


def video(request: Request) -> VideoEngine:
    return request.app.state.video


def forge(request: Request) -> GameForge:
    return request.app.state.games


def library(request: Request) -> LibraryDB:
    return request.app.state.db


def _record(
    request: Request, kind: str, prompt: str, result: dict[str, Any], *,
    title: str = "", url: str = "", payload: dict[str, Any] | None = None,
) -> None:
    """Persist a generation to the library. Never fails a request."""
    try:
        library(request).add(
            kind,
            title=title or "",
            prompt=prompt or "",
            model=str(result.get("model", ""))[:200],
            url=url or _first_url(result),
            payload=payload if payload is not None else {"result": _slim(result)},
        )
    except Exception:  # noqa: BLE001 - storage is a side effect, not the answer
        logging.getLogger(__name__).warning("library write failed", exc_info=True)


def _first_url(result: dict[str, Any]) -> str:
    """The one previewable asset a record has, if any.

    Image and video responses nest their assets in a list, so a top-level `url`
    lookup alone left library cards without an "open" link.
    """
    direct = result.get("url")
    if isinstance(direct, str) and direct:
        return direct
    for key in ("images", "videos"):
        items = result.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    candidate = item.get("url")
                    if isinstance(candidate, str) and candidate:
                        return candidate
    return ""


def _slim(result: dict[str, Any]) -> dict[str, Any]:
    """Trim base64 blobs so a record stays a few KB."""
    slim: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, str) and len(value) > 2000:
            slim[key] = value[:200] + f"…({len(value)} chars)"
        else:
            slim[key] = value
    return slim


# ------------------------------------------------------------------- catalogue
@router.get("/models", response_model=DiscoverResponse, tags=["models"])
async def list_models(request: Request) -> DiscoverResponse:
    models = await gateway(request).describe_all()
    return DiscoverResponse(
        models=[ModelInfo(**m) for m in models],
        count=len(models),
        providers=await gateway(request).provider_status(),
    )


@router.get("/models/{model_name}", response_model=ModelInfo, tags=["models"])
async def get_model(model_name: str, request: Request) -> ModelInfo:
    definition = gateway(request).resolve(model_name)
    info = next(
        m for m in await gateway(request).describe_all() if m["name"] == definition.name
    )
    return ModelInfo(**info)


# ----------------------------------------------------------------------- infer
@router.post("/images/generate", response_model=RunResponse, tags=["inference"])
async def generate_image(body: RunRequest, request: Request) -> RunResponse:
    """Generate an image with the best available model (or an explicit one)."""
    if not body.prompt:
        raise HTTPException(400, "Provide 'prompt'.")
    try:
        width, height = (int(x) for x in body.image_size.lower().split("x"))
    except ValueError as exc:
        raise HTTPException(400, "image_size must look like 1024x1024") from exc
    model = body.extra.get("model") or "auto"
    result = await gateway(request).generate_image(
        body.prompt, model, negative_prompt=body.negative_prompt,
        width=width, height=height, steps=body.steps,
    )
    _record(request, "image", body.prompt, result, title=body.prompt)
    return RunResponse(**result)


@router.post("/models/{model_name}/run", response_model=RunResponse, tags=["inference"])
async def run_model(model_name: str, body: RunRequest, request: Request) -> RunResponse:
    gw = gateway(request)
    definition = gw.resolve(model_name)

    if definition.category == "text":
        messages = body.to_messages()
        if not messages:
            raise HTTPException(400, "Provide 'prompt' or 'messages'.")
        result = await gw.chat(
            messages, definition.name,
            temperature=body.temperature, max_tokens=body.max_tokens, top_p=body.top_p,
        )
        _record(request, "chat", messages[-1]["content"], result)
        return RunResponse(**result)

    if definition.category == "image":
        if not body.prompt:
            raise HTTPException(400, "Provide 'prompt'.")
        try:
            width, height = (int(x) for x in body.image_size.lower().split("x"))
        except ValueError as exc:
            raise HTTPException(400, "image_size must look like 1024x1024") from exc
        result = await gw.generate_image(
            body.prompt, definition.name, negative_prompt=body.negative_prompt,
            width=width, height=height, steps=body.steps,
        )
        _record(request, "image", body.prompt, result, title=body.prompt)
        return RunResponse(**result)

    if definition.category == "embedding":
        if not body.prompt:
            raise HTTPException(400, "Provide 'prompt'.")
        vectors = await gw.embed([body.prompt], definition.name)
        return RunResponse(
            model=definition.name, provider=definition.provider.value, category="embedding",
            raw={"embedding": vectors[0], "dimensions": len(vectors[0])},
        )

    raise HTTPException(400, f"Category '{definition.category}' is not served here.")


@router.post("/chat", tags=["inference"])
async def chat(request: Request, body: RunRequest) -> Any:
    gw = gateway(request)
    messages = body.to_messages()
    if not messages:
        raise HTTPException(400, "Provide 'prompt' or 'messages'.")
    if len(messages[-1]["content"]) > settings.MAX_PROMPT_CHARS:
        raise HTTPException(413, "Prompt too long.")

    if not body.stream:
        result = await gw.chat(
            messages, "auto", temperature=body.temperature, max_tokens=body.max_tokens,
            top_p=body.top_p,
        )
        _record(request, "chat", messages[-1]["content"], result)
        return RunResponse(**result)

    async def events():
        try:
            async for event in gw.stream_chat(
                messages, "auto", temperature=body.temperature, max_tokens=body.max_tokens
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except GatewayError as exc:
            yield f"data: {json.dumps({'event': 'error', 'error': exc.message})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------- skills
@router.get("/skills", response_model=SkillsResponse, tags=["skills"])
async def list_skills(
    request: Request,
    q: str | None = Query(default=None, description="Search terms; ranked with BM25."),
    category: str | None = Query(default=None, description="Restrict to one category."),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SkillsResponse:
    """Search the whole catalogue (2,877 skills, SQLite FTS5). Empty `q` browses."""
    return SkillsResponse(**skills(request).search(q, category, limit, offset))


@router.get("/skills/categories", tags=["skills"])
async def skill_categories(request: Request) -> dict[str, Any]:
    svc = skills(request)
    return {"count": len(svc.db.categories), "categories": svc.db.categories}


@router.get("/skills/search", tags=["skills"])
async def search_skills(
    request: Request,
    q: str = Query(min_length=1),
    category: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SkillsResponse:
    return SkillsResponse(**skills(request).search(q, category, limit, offset))


@router.get("/skills/{skill_name}", tags=["skills"])
async def get_skill(skill_name: str, request: Request) -> dict[str, Any]:
    record = skills(request).db.get(skill_name)
    if record is None:
        raise HTTPException(404, f"No skill named '{skill_name}'.")
    return {**record, "executable": True}


@router.post("/skills/{skill_name}/run", tags=["skills"])
async def run_skill(skill_name: str, body: SkillRequest, request: Request) -> dict[str, Any]:
    result = await skills(request).execute(skill_name, body.inputs or body.params)
    _record(
        request, "skill", json.dumps(body.inputs or body.params)[:4000], result,
        title=skill_name, payload={"skill": skill_name},
    )
    return result


# ----------------------------------------------------------------------- video
@router.get("/video/health", tags=["video"])
async def video_health(request: Request) -> dict[str, Any]:
    return await video(request).health()


@router.post("/video/generate", tags=["video"])
async def generate_video(body: VideoRequest, request: Request) -> dict[str, Any]:
    engine = video(request)
    if body.init_image_b64:
        result = await engine.image_to_video(
            body.init_image_b64, fps=body.fps, motion_bucket_id=body.motion_bucket_id,
            seed=body.seed, steps=body.steps, width=body.width, height=body.height,
            prompt=body.prompt,
        )
        _record(request, "video", body.prompt, result, title=body.prompt)
        return result
    result = await engine.text_to_video(
        body.prompt, negative_prompt=body.negative_prompt, steps=body.steps,
        width=body.width, height=body.height, fps=body.fps,
        motion_bucket_id=body.motion_bucket_id, seed=body.seed,
    )
    _record(request, "video", body.prompt, result, title=body.prompt)
    return result


# ----------------------------------------------------------------------- games
@router.post("/games/generate", tags=["games"])
async def generate_game(body: GameRequest, request: Request) -> dict[str, Any]:
    """Single prompt -> design -> complete playable HTML5 game, served at /games/{file}."""
    try:
        result = await forge(request).generate(body.prompt, model=body.model, genre=body.genre)
    except GatewayError as exc:
        raise HTTPException(502, exc.message) from exc
    _record(
        request, "game", body.prompt, result,
        title=result.get("title") or body.prompt, url=result.get("url", ""),
        payload={k: result.get(k) for k in ("genre", "lines", "bytes", "checks")},
    )
    return result


@router.get("/games", tags=["games"])
async def list_games(limit: int = Query(default=24, ge=1, le=100)) -> dict[str, Any]:
    files = sorted(GAMES_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "count": len(files),
        "games": [
            {
                "filename": f.name,
                "url": f"/games/{f.name}",
                "bytes": f.stat().st_size,
                "title": f.stem.rsplit("-", 2)[0].replace("-", " ").title(),
            }
            for f in files[:limit]
        ],
    }


# ------------------------------------------------------------------ embeddings
@router.post("/embeddings", tags=["inference"])
async def embeddings(request: Request, body: RunRequest) -> dict[str, Any]:
    texts = [body.prompt] if body.prompt else [m.content for m in (body.messages or [])]
    if not texts:
        raise HTTPException(400, "Provide 'prompt' or 'messages'.")
    vectors = await gateway(request).embed(texts)
    return {"model": "nomic-embed-text", "vectors": vectors, "dimensions": len(vectors[0])}


# -------------------------------------------------------------------- library
@router.get("/db/status", tags=["library"])
async def db_status(request: Request) -> dict[str, Any]:
    """Which free backend is live, and where the records are."""
    return library(request).status()


@router.get("/library", tags=["library"])
async def search_library(
    request: Request,
    q: str = Query(default="", description="Full-text query; empty browses newest first."),
    kind: str = Query(default="", description="chat | image | video | game | skill"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return library(request).search(q, kind=kind, limit=limit, offset=offset)


@router.get("/library/{record_id}", tags=["library"])
async def get_record(record_id: str, request: Request) -> dict[str, Any]:
    record = library(request).get(record_id)
    if record is None:
        raise HTTPException(404, f"No record '{record_id}'.")
    return record


@router.delete("/library/{record_id}", tags=["library"])
async def delete_record(record_id: str, request: Request) -> dict[str, Any]:
    if not library(request).delete(record_id):
        raise HTTPException(404, f"No record '{record_id}'.")
    return {"deleted": record_id}
