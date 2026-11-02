"""Agentic skills.

Each skill is a real function with a typed contract, executed with whichever LLM
the gateway can reach. The optional remote index only adds *descriptions* of
community skills; execution always goes through code defined here, so a
compromised index can never run arbitrary logic.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.errors import BadRequest
from app.services.universal_gateway import UniversalGateway

logger = logging.getLogger(__name__)

SkillFn = Callable[[dict[str, Any], UniversalGateway], Awaitable[dict[str, Any]]]

DEFAULT_SKILLS: list[dict[str, Any]] = [
    {
        "name": "web-research",
        "title": "Web research",
        "category": "research",
        "description": "Search the web, read the top pages, and return a cited summary.",
        "params": ["query", "max_results"],
        "fields": [
            {"name": "query", "label": "Research question", "type": "textarea", "required": True,
             "placeholder": "What changed in EU AI regulation in 2026?"},
            {"name": "max_results", "label": "Pages to read", "type": "number", "default": 3},
        ],
    },
    {
        "name": "code-generator",
        "title": "Code generator",
        "category": "development",
        "description": "Generate a single-file program from a specification.",
        "params": ["prompt", "language"],
        "fields": [
            {"name": "prompt", "label": "Specification", "type": "textarea", "required": True,
             "placeholder": "A CLI that renames photos by EXIF date."},
            {"name": "language", "label": "Language", "type": "text", "default": "python"},
        ],
    },
    {
        "name": "doc-summarizer",
        "title": "Document summariser",
        "category": "utility",
        "description": "Condense long text into bullet points with the key numbers kept.",
        "params": ["text", "max_bullets"],
        "fields": [
            {"name": "text", "label": "Text to summarise", "type": "textarea", "required": True},
            {"name": "max_bullets", "label": "Bullets", "type": "number", "default": 5},
        ],
    },
    {
        "name": "extract-json",
        "title": "Structured extraction",
        "category": "data",
        "description": "Pull structured fields out of messy text and return JSON.",
        "params": ["text", "schema"],
        "fields": [
            {"name": "text", "label": "Source text", "type": "textarea", "required": True},
            {"name": "schema", "label": "Target schema (JSON object)", "type": "textarea",
             "required": True, "default": '{"company": "string", "revenue": "number"}'},
        ],
    },
    {
        "name": "translator",
        "title": "Translator",
        "category": "language",
        "description": "Translate text, preserving tone and technical terms.",
        "params": ["text", "target_language"],
        "fields": [
            {"name": "text", "label": "Text", "type": "textarea", "required": True},
            {"name": "target_language", "label": "Target language", "type": "text",
             "required": True, "default": "French"},
        ],
    },
]


class AgenticSkillsService:
    def __init__(self, gateway: UniversalGateway | None = None) -> None:
        self.gateway = gateway or UniversalGateway()
        self._registry: dict[str, SkillFn] = {
            "web-research": self._skill_web_research,
            "code-generator": self._skill_code_generator,
            "doc-summarizer": self._skill_doc_summarizer,
            "extract-json": self._skill_extract_json,
            "translator": self._skill_translator,
        }

    async def close(self) -> None:
        await self.gateway.close()

    # ------------------------------------------------------------------ listing
    async def list_skills(self) -> tuple[list[dict[str, Any]], str]:
        """Merge the built-in catalogue with the remote index (descriptions only)."""
        remote_source = "builtin"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(settings.SKILLS_INDEX_URL)
                r.raise_for_status()
                payload = r.json()
            items = payload if isinstance(payload, list) else payload.get("skills", [])
            by_name = {s["name"]: s for s in DEFAULT_SKILLS}
            for item in items:
                name = item.get("name") or item.get("slug")
                if not name:
                    continue
                if name in by_name:
                    by_name[name].setdefault("description", item.get("description", ""))
                else:
                    by_name[name] = {
                        "name": name,
                        "title": item.get("title") or name.replace("-", " ").title(),
                        "category": item.get("category", "community"),
                        "description": item.get("description", ""),
                        "params": item.get("params", []),
                        "fields": item.get("fields", []),
                        "executable": False,
                    }
            merged = []
            for name, entry in by_name.items():
                entry = dict(entry)
                entry.setdefault("title", name.replace("-", " ").title())
                entry.setdefault("fields", [])
                entry["executable"] = name in self._registry
                merged.append(entry)
            return merged, "builtin+remote"
        except Exception as exc:  # noqa: BLE001 - the index is best effort
            logger.info("skills index unavailable (%s); using built-in catalogue", exc)
            return [dict(s, executable=True) for s in DEFAULT_SKILLS], remote_source

    # ---------------------------------------------------------------- execution
    async def execute(self, skill_name: str, params: dict[str, Any]) -> dict[str, Any]:
        fn = self._registry.get(skill_name)
        if fn is None:
            raise BadRequest(
                f"Skill '{skill_name}' has no executor. Executable: {sorted(self._registry)}"
            )
        result = await fn(params, self.gateway)
        return {"skill": skill_name, **result}

    # ------------------------------------------------------------------ skills
    async def _skill_web_research(self, params: dict[str, Any], gw: UniversalGateway) -> dict:
        query = (params.get("query") or "").strip()
        if not query:
            raise BadRequest("web-research needs 'query'.")
        max_results = max(1, min(int(params.get("max_results", 5)), 10))
        results = await search_duckduckgo(query, max_results)
        if not results:
            return {"query": query, "results": [], "summary": "No results found.", "citations": []}

        excerpts = []
        for r in results[:3]:
            page = await fetch_text(r["url"])
            if page:
                excerpts.append(f"[{r['title']}]({r['url']})\n{page[:2500]}")

        corpus = "\n\n".join(excerpts) or "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in results
        )
        prompt = (
            f"Research question: {query}\n\nSources:\n{corpus}\n\n"
            "Write a factual summary in under 200 words. Use inline [n] markers that map to the "
            "numbered sources. If the sources disagree, say so. If they don't answer the "
            "question, say that plainly."
        )
        summary = await gw.complete(prompt, max_tokens=600)
        return {
            "query": query,
            "results": results,
            "summary": summary,
            "citations": [{"n": i + 1, "title": r["title"], "url": r["url"]} for i, r in enumerate(results)],
        }

    async def _skill_code_generator(self, params: dict[str, Any], gw: UniversalGateway) -> dict:
        prompt = (params.get("prompt") or "").strip()
        language = (params.get("language") or "python").strip()
        if not prompt:
            raise BadRequest("code-generator needs 'prompt'.")
        code = await gw.complete(
            f"Write production-quality {language} code for the following. "
            f"Return only code inside a single fenced block, with no commentary.\n\n{prompt}",
            max_tokens=1500,
        )
        return {"language": language, "code": strip_fences(code)}

    async def _skill_doc_summarizer(self, params: dict[str, Any], gw: UniversalGateway) -> dict:
        text = (params.get("text") or "").strip()
        if len(text) < 40:
            raise BadRequest("doc-summarizer needs at least 40 characters of 'text'.")
        bullets = max(3, min(int(params.get("max_bullets", 5)), 12))
        summary = await gw.complete(
            f"Summarise the text below in exactly {bullets} bullets. Keep every number, date and "
            f"name that matters. No preamble.\n\n{text[:24000]}",
            max_tokens=800,
        )
        return {"summary": summary, "word_count": len(text.split())}

    async def _skill_extract_json(self, params: dict[str, Any], gw: UniversalGateway) -> dict:
        text = (params.get("text") or "").strip()
        schema = params.get("schema")
        if not text:
            raise BadRequest("extract-json needs 'text'.")
        if not schema:
            raise BadRequest("extract-json needs 'schema' (a JSON object describing the fields).")
        schema_str = schema if isinstance(schema, str) else json.dumps(schema)
        raw = await gw.complete(
            "Extract data from the text according to the JSON schema. Reply with JSON only; use "
            f"null for missing values. Schema: {schema_str}\n\nText:\n{text[:16000]}",
            max_tokens=1200,
        )
        cleaned = strip_fences(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            data = json.loads(match.group(0)) if match else None
        return {"data": data, "parsed": data is not None}

    async def _skill_translator(self, params: dict[str, Any], gw: UniversalGateway) -> dict:
        text = (params.get("text") or "").strip()
        target = (params.get("target_language") or "").strip()
        if not text or not target:
            raise BadRequest("translator needs 'text' and 'target_language'.")
        translated = await gw.complete(
            f"Translate into {target}. Preserve formatting, names and technical terms. "
            f"Return the translation only.\n\n{text[:16000]}",
            max_tokens=2000,
        )
        return {"target_language": target, "translation": translated.strip()}


# --------------------------------------------------------------------- helpers
def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```[\w+-]*\n(.*?)\n```$", text, re.DOTALL)
    return match.group(1) if match else text


async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Scrape the no-JS DuckDuckGo endpoint. No API key, no tracking."""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.post(
                "https://html.duckduckgo.com/html/", data={"q": query}, headers=headers
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("duckduckgo search failed: %s", exc)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    out: list[dict[str, str]] = []
    for result in soup.select("div.result"):
        link = result.select_one("a.result__a")
        snippet = result.select_one(".result__snippet")
        if not link:
            continue
        href = link.get("href", "")
        if "uddg=" in href:  # unwrap DDG's redirect
            qs = parse_qs(urlparse(href).query)
            href = unquote(qs.get("uddg", [href])[0])
        if not href.startswith("http"):
            continue
        out.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": href,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )
        if len(out) >= max_results:
            break
    return out


async def fetch_text(url: str, limit: int = 200_000) -> str:
    """Fetch a page and return readable text. Skips non-HTML and private hosts."""
    host = urlparse(url).hostname or ""
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return ""
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; RoolBot/1.0)"})
            if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                return ""
            soup = BeautifulSoup(r.text[:limit], "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
                tag.decompose()
            return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    except httpx.HTTPError:
        return ""
