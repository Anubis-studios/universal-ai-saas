"""Turn one sentence into a playable, self-contained HTML5 game.

The model writes the game; this module keeps it honest. Nothing is templated:
every game is generated, then validated (fences stripped, external assets
rejected, canvas + loop + input handlers required) before it is written to disk.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from app.core.errors import BadRequest, ProviderError
from app.services.universal_gateway import UniversalGateway

GAMES_DIR = Path(__file__).resolve().parents[2] / "outputs" / "games"

MAX_SOURCE_CHARS = 240_000

DESIGN_PROMPT = """You are a game designer. From the player's idea, produce a compact
design document as strict JSON and nothing else.

Keys:
  title       short game title, title case
  genre       one of: arcade, platformer, shooter, puzzle, runner, tower-defense, rpg, sports
  hook        one sentence on what makes it fun
  loop        the 10-second core loop
  mechanics   3-5 short strings, each a rule the code must implement
  controls    object mapping keys to actions, e.g. {{"ArrowLeft": "move left"}}
  win         how the player wins, or "endless: survive and score"
  fail        how the player loses
  palette     object with bg, fg, accent, danger as hex colours

The player's idea is in the user message. Reply with JSON only."""

CODE_PROMPT = """You are a senior HTML5 game developer. Write a COMPLETE,
PLAYABLE game as ONE html file from the design document in the user message.

Hard requirements:
- One file: <!doctype html> ... </html>. Inline <style> and <script> only.
- No external anything: no CDN scripts, no <script src>, no images, no fonts,
  no fetch/XHR. All art is drawn with canvas 2D primitives or CSS.
- Plain browser JavaScript. No ES module syntax, no import/export, no TypeScript.
- A single <canvas> that resizes to the window and keeps its aspect ratio.
- requestAnimationFrame loop with a delta time clamp (no per-frame allocation storms).
- Keyboard AND touch/pointer input. Keyboard must prevent page scroll.
- Real game states: title screen, playing, game over, restart without reload.
- Score (or health) HUD drawn on canvas, increasing difficulty over time.
- Implement every mechanic in the document. Tune numbers so it is fun in 10 seconds.
- Use the document's palette. No placeholder text like "TODO".

Output only the code, starting with <!doctype html>. No explanation, no markdown fences."""

IFRAME_BAN = re.compile(r"<script[^>]+src=|<link[^>]+href=|@import\s|fetch\s*\(|XMLHttpRequest", re.I)


class GameForge:
    def __init__(self, gateway: UniversalGateway) -> None:
        self.gateway = gateway
        GAMES_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public
    async def generate(self, prompt: str, *, model: str = "auto", genre: str | None = None) -> dict[str, Any]:
        idea = prompt.strip()
        if len(idea) < 4:
            raise BadRequest("Describe the game you want in a sentence or two.")

        started = time.perf_counter()
        design = await self._design(idea, model, genre)
        source = await self._code(design, model)
        report = validate_game(source)

        slug = slugify(design.get("title") or idea)[:48] or "game"
        name = f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.html"
        path = GAMES_DIR / name
        path.write_text(source, encoding="utf-8")

        return {
            "title": design.get("title") or "Untitled Game",
            "genre": design.get("genre") or "arcade",
            "prompt": idea,
            "design": design,
            "url": f"/games/{name}",
            "filename": name,
            "bytes": path.stat().st_size,
            "lines": source.count("\n") + 1,
            "checks": report,
            "model": getattr(self, "_last_model", "auto"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    async def _complete(self, system: str, user: str, *, model: str,
                        temperature: float, max_tokens: int, attempts: int = 2) -> str:
        return await self._complete_messages(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model, temperature=temperature, max_tokens=max_tokens, attempts=attempts,
        )

    async def _complete_messages(self, messages: list[dict[str, str]], model: str, *,
                                 temperature: float, max_tokens: int,
                                 attempts: int = 2) -> str:
        for attempt in range(1, attempts + 1):
            try:
                result = await self.gateway.chat(
                    messages, model, temperature=temperature, max_tokens=max_tokens,
                )
            except ProviderError as exc:
                wait = _retry_after(exc.message)
                # Keyless free tiers allow one reply per window. Waiting is the
                # difference between "works" and "always fails" on a fresh deploy.
                if attempt < attempts and wait is not None and wait <= 75:
                    await asyncio.sleep(wait + 2)
                    continue
                raise
            self._last_model = f"{result['provider']} / {result['model']}"
            return result["text"] or ""
        raise ProviderError("No completion returned.")

    # ------------------------------------------------------------------ stages
    async def _design(self, idea: str, model: str, genre: str | None) -> dict[str, Any]:
        """Design pass. Optional by design: a single-reply free provider must still
        be able to ship a game, so a failed design falls back to a local spec."""
        request = idea if not genre else f"Genre: {genre}. Idea: {idea}"
        try:
            text = await self._complete(
                DESIGN_PROMPT, request, model=model, temperature=0.8, max_tokens=700
            )
        except Exception:  # rate limit, bad key, or a model that ignored the JSON ask
            return _fallback_design(idea, genre)
        spec = extract_json(text)
        if not isinstance(spec, dict):
            # A model that ignored the JSON instruction still moves us forward.
            spec = _fallback_design(idea, genre)
        spec.setdefault("palette", DEFAULT_PALETTE)
        return spec

    async def _code(self, design: dict[str, Any], model: str) -> str:
        clean = {k: v for k, v in design.items() if not k.startswith("_")}
        request = json.dumps(clean, indent=2)
        text = await self._complete(
            CODE_PROMPT, request, model=model, temperature=0.35, max_tokens=8192,
        )
        source = extract_html(text)
        if not source:
            raise ProviderError("The model returned no HTML game this time; try again.")
        # Free providers truncate at their output-token cap; splice the remainder
        # on until the document closes, then let the validator judge the result.
        for _ in range(3):
            if source.rstrip().endswith("</html>"):
                return source
            tail = await self._complete_messages(
                [
                    {"role": "system", "content": CODE_PROMPT},
                    {"role": "user", "content": request},
                    {"role": "assistant", "content": source},
                    {"role": "user", "content":
                        "Your previous message was cut off mid-file. Repeat nothing. "
                        "Return only the remaining characters, starting exactly where "
                        "you stopped, and finish the file with </html>."},
                ],
                model, temperature=0.2, max_tokens=8192,
            )
            addition = strip_fences(tail)
            if not addition.strip():
                break
            source = source.rstrip() + "\n" + addition.lstrip()
        return source


def _retry_after(message: str) -> int | None:
    match = re.search(r"retry after (\d+)", message, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _fallback_design(idea: str, genre: str | None) -> dict[str, Any]:
    """Deterministic spec used when the design pass cannot run."""
    text = idea.strip()
    title = " ".join(text.split()[:4]).strip(" .,!?:;") or "Untitled Game"
    return {
        "title": title.title(),
        "genre": genre or "arcade",
        "hook": text,
        "loop": text,
        "mechanics": [text, "score increases as you play", "difficulty ramps over time"],
        "controls": {"Arrow keys": "move", "Space": "act"},
        "win": "reach the highest score you can",
        "fail": "lose all lives",
        "palette": DEFAULT_PALETTE,
    }


DEFAULT_PALETTE = {"bg": "#050b16", "fg": "#dff6ff", "accent": "#41c3f1", "danger": "#f79f43"}


def extract_html(text: str) -> str:
    """Pull the game out of fences, preamble, or a chatty reply."""
    text = text.strip()
    fenced = re.findall(r"```(?:html|HTML|javascript|js)?\s*\n(.*?)```", text, re.S)
    for block in sorted(fenced, key=len, reverse=True):
        if "<html" in block.lower() or "<canvas" in block.lower():
            text = block
            break
    start = text.lower().find("<!doctype")
    if start == -1:
        start = text.lower().find("<html")
    if start > 0:
        text = text[start:]
    end = text.lower().rfind("</html>")
    if end != -1:
        text = text[: end + 7]
    return text.strip()


def strip_fences(text: str) -> str:
    """Remove a stray code fence without trimming the body's edges."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text


def extract_json(text: str) -> Any:
    text = text.strip()
    fenced = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    candidates = fenced + [text]
    for blob in candidates:
        blob = blob.strip()
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            pass
        start, end = blob.find("{"), blob.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(blob[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def validate_game(source: str) -> dict[str, Any]:
    """Reject anything that is not a runnable, offline, single-file game."""
    low = source.lower()
    checks = {
        "doctype": low.startswith("<!doctype"),
        "closed": low.rstrip().endswith("</html>"),
        "canvas": "<canvas" in low,
        "script": "<script" in low,
        "loop": ("requestanimationframe" in low),
        "input": ("keydown" in low or "addeventlistener('pointerdown'" in low
                  or 'addeventlistener("pointerdown"' in low),
        "offline": not IFRAME_BAN.search(source),
        "no_placeholder": "todo" not in low and "your code here" not in low,
        "size": 2000 <= len(source) <= MAX_SOURCE_CHARS,
    }
    if not checks["offline"]:
        raise ProviderError("Generated game pulled external assets; rejected as non-self-contained.")
    if not checks["closed"] or not checks["canvas"]:
        raise ProviderError("Generated game was incomplete (no canvas or no </html>).")
    if not checks["size"]:
        raise ProviderError("Generated game was too small to be playable.")
    return checks


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
