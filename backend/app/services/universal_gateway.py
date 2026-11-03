"""Universal model gateway.

One async interface over many providers. Every model is described by a
ModelDefinition in a single registry, and each provider has one adapter, so
adding a model is a registry entry, not new plumbing.

Design rules:
  * Credentials come from settings only; nothing is hard-coded.
  * A model without credentials is listed as unavailable, never attempted.
  * `auto` transparently falls through the registry in preference order.
  * Streaming is real SSE pass-through for text models that support it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.errors import (
    BadRequest,
    ModelNotFound,
    ProviderError,
    ProviderNotConfigured,
)
from app.services.media_store import save_b64

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    GROQ = "groq"
    GOOGLE = "google"
    MISTRAL = "mistral"
    OPENROUTER = "openrouter"
    TOGETHER = "together"
    CEREBRAS = "cerebras"
    HUGGINGFACE = "huggingface"
    POLLINATIONS = "pollinations"
    OLLAMA = "ollama"
    STABLE_DIFFUSION = "stable_diffusion"


OPENAI_COMPATIBLE = {
    ProviderType.GROQ: "https://api.groq.com/openai/v1",
    ProviderType.MISTRAL: "https://api.mistral.ai/v1",
    ProviderType.OPENROUTER: "https://openrouter.ai/api/v1",
    ProviderType.TOGETHER: "https://api.together.xyz/v1",
    ProviderType.CEREBRAS: "https://api.cerebras.ai/v1",
    # Pollinations' OpenAI-compatible endpoint *is* the chat route, no /chat/completions.
    ProviderType.POLLINATIONS: "https://text.pollinations.ai/openai",
}

KEY_ATTR = {
    ProviderType.GROQ: "GROQ_API_KEY",
    ProviderType.GOOGLE: "GOOGLE_API_KEY",
    ProviderType.MISTRAL: "MISTRAL_API_KEY",
    ProviderType.OPENROUTER: "OPENROUTER_API_KEY",
    ProviderType.TOGETHER: "TOGETHER_API_KEY",
    ProviderType.CEREBRAS: "CEREBRAS_API_KEY",
    ProviderType.HUGGINGFACE: "HUGGINGFACE_API_KEY",
    ProviderType.POLLINATIONS: "POLLINATIONS_API_KEY",
}


@dataclass(frozen=True)
class ModelDefinition:
    name: str
    provider: ProviderType
    category: str
    model_id: str
    is_free: bool = True
    requires_key: bool = False
    capabilities: tuple[str, ...] = ("text",)
    context_window: int | None = None
    description: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- registry
UNIVERSAL_REGISTRY: dict[str, ModelDefinition] = {
    # ---- Groq: free tier, fast LPU inference -------------------------------
    "llama-3.3-70b": ModelDefinition(
        "llama-3.3-70b", ProviderType.GROQ, "text", "llama-3.3-70b-versatile", True, True,
        ("text", "stream", "tools"), 128_000, "Best general-purpose Llama on Groq.",
    ),
    "llama-3.1-8b": ModelDefinition(
        "llama-3.1-8b", ProviderType.GROQ, "text", "llama-3.1-8b-instant", True, True,
        ("text", "stream", "tools"), 128_000, "Cheapest and fastest; good for routing and extraction.",
    ),
    "mixtral-8x7b": ModelDefinition(
        "mixtral-8x7b", ProviderType.GROQ, "text", "mixtral-8x7b-32768", True, True,
        ("text", "stream"), 32_768, "Mixture-of-experts, strong multilingual output.",
    ),
    "qwen3-32b": ModelDefinition(
        "qwen3-32b", ProviderType.GROQ, "text", "qwen/qwen3-32b", True, True,
        ("text", "stream", "reasoning"), 131_072, "Qwen 3 with visible reasoning mode.",
    ),
    # ---- Google Gemini: free tier -----------------------------------------
    "gemini-2.0-flash": ModelDefinition(
        "gemini-2.0-flash", ProviderType.GOOGLE, "text", "gemini-2.0-flash", True, True,
        ("text", "stream", "vision"), 1_000_000, "Long context and image understanding.",
    ),
    "gemini-1.5-flash": ModelDefinition(
        "gemini-1.5-flash", ProviderType.GOOGLE, "text", "gemini-1.5-flash", True, True,
        ("text", "stream", "vision"), 1_000_000, "Cheap multimodal workhorse.",
    ),
    # ---- Mistral: free experimental tier ---------------------------------
    "mistral-small": ModelDefinition(
        "mistral-small", ProviderType.MISTRAL, "text", "mistral-small-latest", True, True,
        ("text", "stream", "tools"), 32_768, "Balanced European model.",
    ),
    "open-mistral-nemo": ModelDefinition(
        "open-mistral-nemo", ProviderType.MISTRAL, "text", "open-mistral-nemo", True, True,
        ("text", "stream"), 128_000, "Apache-2.0 12B model.",
    ),
    # ---- Cerebras: very high tokens/sec free tier ------------------------
    "llama3.1-8b-cerebras": ModelDefinition(
        "llama3.1-8b-cerebras", ProviderType.CEREBRAS, "text", "llama3.1-8b", True, True,
        ("text", "stream"), 8_192, "Sub-second latency, good for agents.",
    ),
    # ---- OpenRouter: free model pool -------------------------------------
    "openrouter/llama-3.3-70b": ModelDefinition(
        "openrouter/llama-3.3-70b", ProviderType.OPENROUTER, "text",
        "meta-llama/llama-3.3-70b-instruct:free", True, True,
        ("text", "stream"), 128_000, "Free pool route; availability varies.",
    ),
    "openrouter/deepseek-r1": ModelDefinition(
        "openrouter/deepseek-r1", ProviderType.OPENROUTER, "text",
        "deepseek/deepseek-r1:free", True, True,
        ("text", "stream", "reasoning"), 64_000, "Open reasoning model via the free pool.",
    ),
    # ---- Self-hosted Ollama ----------------------------------------------
    "llama3.1:8b": ModelDefinition(
        "llama3.1:8b", ProviderType.OLLAMA, "text", "llama3.1:8b", True, False,
        ("text", "stream"), 128_000, "Local, keyless, private.",
    ),
    "qwen2.5:7b": ModelDefinition(
        "qwen2.5:7b", ProviderType.OLLAMA, "text", "qwen2.5:7b", True, False,
        ("text", "stream"), 32_768, "Local multilingual model.",
    ),
    "mistral:7b": ModelDefinition(
        "mistral:7b", ProviderType.OLLAMA, "text", "mistral:7b", True, False,
        ("text", "stream"), 32_768, "Local Mistral.",
    ),
    "nomic-embed-text": ModelDefinition(
        "nomic-embed-text", ProviderType.OLLAMA, "embedding", "nomic-embed-text", True, False,
        ("embedding",), 8_192, "Local embeddings for RAG.",
    ),
    # ---- Images -----------------------------------------------------------
    "pollinations-openai": ModelDefinition(
        "pollinations-openai", ProviderType.POLLINATIONS, "text", "openai", True, False,
        ("text", "stream"), 32_768, "Keyless GPT-OSS 20B. Works with no account at all.",
    ),
    # ---- Images -----------------------------------------------------------
    "pollinations-flux": ModelDefinition(
        "pollinations-flux", ProviderType.POLLINATIONS, "image", "flux", True, False,
        ("image",), None, "Keyless FLUX image generation. No account required.",
    ),
    "sdxl-turbo": ModelDefinition(
        "sdxl-turbo", ProviderType.HUGGINGFACE, "image", "stabilityai/sdxl-turbo", True, True,
        ("image",), None, "1-4 step SDXL. Fast previews.",
        {"default_steps": 4},
    ),
    "flux-schnell": ModelDefinition(
        "flux-schnell", ProviderType.HUGGINGFACE, "image", "black-forest-labs/FLUX.1-schnell",
        True, True, ("image",), None, "FLUX.1 schnell, 4 steps, Apache-2.0.",
        {"default_steps": 4},
    ),
    "sd-webui": ModelDefinition(
        "sd-webui", ProviderType.STABLE_DIFFUSION, "image", "local-checkpoint", True, False,
        ("image", "img2img", "upscale"), None, "Your own Stable Diffusion WebUI instance.",
    ),
}

# Preference order used by `auto` and by fallback.
AUTO_ORDER = [
    "llama-3.3-70b",
    "gemini-2.0-flash",
    "mistral-small",
    "llama-3.1-8b",
    "openrouter/llama-3.3-70b",
    "pollinations-openai",
    "llama3.1:8b",
    "mistral:7b",
]


IMAGE_PREFERENCE = ["flux-schnell", "sdxl-turbo", "sd-webui", "pollinations-flux"]


class UniversalGateway:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS, connect=10.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- discovery
    def key_for(self, provider: ProviderType) -> str | None:
        attr = KEY_ATTR.get(provider)
        return getattr(settings, attr, None) if attr else None

    async def is_available(self, definition: ModelDefinition) -> bool:
        if definition.provider is ProviderType.OLLAMA:
            return await self._ollama_has(definition.model_id)
        if definition.provider is ProviderType.STABLE_DIFFUSION:
            try:
                r = await self._client.get(f"{settings.STABLE_DIFFUSION_URL}/sdapi/v1/options", timeout=3.0)
                return r.status_code < 500
            except httpx.HTTPError:
                return False
        if definition.requires_key:
            return bool(self.key_for(definition.provider))
        return True

    async def _ollama_has(self, model_id: str) -> bool:
        try:
            r = await self._client.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3.0)
            r.raise_for_status()
            names = {m.get("name", "") for m in r.json().get("models", [])}
            return model_id in names or f"{model_id}:latest" in names
        except httpx.HTTPError:
            return False

    async def describe_all(self) -> list[dict[str, Any]]:
        defs = list(UNIVERSAL_REGISTRY.values())
        availability = await asyncio.gather(*(self.is_available(d) for d in defs))
        out = []
        for d, ok in zip(defs, availability):
            out.append(
                {
                    "name": d.name,
                    "provider": d.provider.value,
                    "category": d.category,
                    "model_id": d.model_id,
                    "is_free": d.is_free,
                    "requires_key": d.requires_key,
                    "available": ok,
                    "capabilities": list(d.capabilities),
                    "context_window": d.context_window,
                    "description": d.description,
                }
            )
        return out

    async def provider_status(self) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for provider in ProviderType:
            if provider in (ProviderType.OLLAMA, ProviderType.STABLE_DIFFUSION):
                continue
            if provider is ProviderType.POLLINATIONS:
                status[provider.value] = True
                continue
            status[provider.value] = bool(self.key_for(provider))
        status["ollama"] = await self._ollama_has("")
        return status

    # -------------------------------------------------------------- selection
    def resolve(self, name: str) -> ModelDefinition:
        if name in UNIVERSAL_REGISTRY:
            return UNIVERSAL_REGISTRY[name]
        lowered = name.lower()
        for key, definition in UNIVERSAL_REGISTRY.items():
            if key.lower() == lowered or definition.model_id.lower() == lowered:
                return definition
        raise ModelNotFound(
            f"Unknown model '{name}'. GET {settings.API_PREFIX}/models for the catalogue."
        )

    async def _candidates(self, name: str, category: str) -> list[ModelDefinition]:
        if name != "auto":
            first = self.resolve(name)
            if first.category != category:
                raise BadRequest(f"Model '{name}' is a {first.category} model, not {category}.")
            pool = [first] + [
                UNIVERSAL_REGISTRY[n]
                for n in AUTO_ORDER
                if n in UNIVERSAL_REGISTRY and UNIVERSAL_REGISTRY[n].category == category
            ]
        else:
            pool = [UNIVERSAL_REGISTRY[n] for n in AUTO_ORDER if n in UNIVERSAL_REGISTRY]
        unique: dict[str, ModelDefinition] = {}
        for definition in pool:
            unique.setdefault(definition.name, definition)
        return list(unique.values())

    # ------------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "auto",
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        top_p: float = 1.0,
        allow_fallback: bool = True,
    ) -> dict[str, Any]:
        if not messages:
            raise BadRequest("At least one message is required.")
        candidates = await self._candidates(model, "text")
        errors: list[str] = []
        for definition in candidates:
            if not await self.is_available(definition):
                errors.append(f"{definition.name}: not available (missing key or endpoint)")
                if model != "auto":
                    continue
                continue
            started = time.perf_counter()
            try:
                text, usage = await self._dispatch_chat(
                    definition, messages, temperature, max_tokens, top_p
                )
            except ProviderError as exc:
                errors.append(f"{definition.name}: {exc.message}")
                if model != "auto" and not allow_fallback:
                    raise
                continue
            if not text.strip():
                errors.append(f"{definition.name}: empty completion")
                continue
            return {
                "model": definition.name,
                "provider": definition.provider.value,
                "category": "text",
                "text": text,
                "usage": usage,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        raise ProviderError("No text model could serve this request. " + " | ".join(errors[:6]))

    async def complete(self, prompt: str, *, max_tokens: int = 800, **kwargs: Any) -> str:
        result = await self.chat(
            [{"role": "user", "content": prompt}], kwargs.pop("model", "auto"),
            max_tokens=max_tokens, **kwargs,
        )
        return result["text"] or ""

    async def _dispatch_chat(
        self, d: ModelDefinition, messages: list[dict[str, str]],
        temperature: float, max_tokens: int, top_p: float,
    ) -> tuple[str, dict[str, Any]]:
        if d.provider is ProviderType.GOOGLE:
            return await self._google_chat(d, messages, temperature, max_tokens)
        if d.provider is ProviderType.OLLAMA:
            return await self._ollama_chat(d, messages, temperature, max_tokens)
        return await self._openai_chat(d, messages, temperature, max_tokens, top_p)

    def _headers(self, provider: ProviderType) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        key = self.key_for(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if provider in (ProviderType.OPENROUTER, ProviderType.POLLINATIONS):
            headers |= {"HTTP-Referer": "https://rool.cloud", "X-Title": settings.APP_NAME}
        return headers

    async def _openai_chat(self, d, messages, temperature, max_tokens, top_p):
        base = OPENAI_COMPATIBLE[d.provider]
        payload = {
            "model": d.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": False,
        }
        url = base if d.provider is ProviderType.POLLINATIONS else f"{base}/chat/completions"
        try:
            r = await self._client.post(url, json=payload, headers=self._headers(d.provider))
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc}", provider=d.provider.value) from exc
        if r.status_code in (402, 429):
            raise ProviderError(
                f"rate limited (free tier): {self._error_detail(r)}",
                provider=d.provider.value, status_code=r.status_code,
            )
        if r.status_code >= 400:
            raise ProviderError(self._error_detail(r), provider=d.provider.value)
        body = r.json()
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError("malformed provider response", provider=d.provider.value) from exc
        return text, body.get("usage", {})

    async def _google_chat(self, d, messages, temperature, max_tokens):
        key = self.key_for(ProviderType.GOOGLE)
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{d.model_id}:generateContent"
        )
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            r = await self._client.post(url, params={"key": key}, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc}", provider="google") from exc
        if r.status_code >= 400:
            raise ProviderError(self._error_detail(r), provider="google")
        body = r.json()
        try:
            parts = body["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError) as exc:
            reason = body.get("promptFeedback", {}).get("blockReason")
            raise ProviderError(f"empty response{'; blocked: ' + reason if reason else ''}",
                               provider="google") from exc
        usage = body.get("usageMetadata", {})
        return text, usage

    async def _ollama_chat(self, d, messages, temperature, max_tokens):
        payload = {
            "model": d.model_id,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            r = await self._client.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama error: {exc}", provider="ollama") from exc
        body = r.json()
        usage = {
            "prompt_tokens": body.get("prompt_eval_count"),
            "completion_tokens": body.get("eval_count"),
        }
        return body.get("message", {}).get("content", ""), usage

    @staticmethod
    def _error_detail(r: httpx.Response) -> str:
        try:
            body = r.json()
            detail = body.get("error", body)
            if isinstance(detail, dict):
                detail = detail.get("message", json.dumps(detail))
            return f"HTTP {r.status_code}: {str(detail)[:300]}"
        except ValueError:
            return f"HTTP {r.status_code}: {r.text[:200]}"

    # --------------------------------------------------------------- streaming
    async def stream_chat(
        self, messages: list[dict[str, str]], model: str = "auto",
        *, temperature: float = 0.7, max_tokens: int = 1024,
    ) -> AsyncIterator[dict[str, Any]]:
        candidates = await self._candidates(model, "text")
        for definition in candidates:
            if not await self.is_available(definition):
                continue
            try:
                async for event in self._stream_one(definition, messages, temperature, max_tokens):
                    yield event
                return
            except ProviderError as exc:
                if model != "auto":
                    yield {"event": "error", "error": exc.message, "provider": definition.provider.value}
                    return
                logger.info("stream fallback from %s: %s", definition.name, exc.message)
                continue
        yield {"event": "error", "error": "No streaming model available."}

    async def _stream_one(self, d, messages, temperature, max_tokens) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "start", "model": d.name, "provider": d.provider.value}

        if d.provider is ProviderType.OLLAMA:
            payload = {
                "model": d.model_id, "messages": messages, "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            async with self._client.stream(
                "POST", f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        yield {"event": "delta", "text": delta}
                    if chunk.get("done"):
                        yield {"event": "done", "model": d.name, "provider": d.provider.value}
                        return
            return

        if d.provider is ProviderType.GOOGLE:
            key = self.key_for(ProviderType.GOOGLE)
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{d.model_id}:streamGenerateContent?alt=sse"
            )
            contents = [
                {"role": "model" if m["role"] == "assistant" else "user",
                 "parts": [{"text": m["content"]}]}
                for m in messages
            ]
            payload = {"contents": contents,
                       "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
            async with self._client.stream(
                "POST", url, params={"key": key}, json=payload
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        body = json.loads(data)
                        parts = body["candidates"][0]["content"]["parts"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    for part in parts:
                        if part.get("text"):
                            yield {"event": "delta", "text": part["text"]}
            yield {"event": "done", "model": d.name, "provider": d.provider.value}
            return

        payload = {
            "model": d.model_id, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "stream": True,
        }
        base = OPENAI_COMPATIBLE[d.provider]
        url = base if d.provider is ProviderType.POLLINATIONS else f"{base}/chat/completions"
        usage: dict[str, Any] = {}
        for attempt in range(2):
            try:
                async for event in self._stream_openai_lines(url, payload, d, usage):
                    yield event
                return
            except ProviderError:
                if attempt or d.provider is not ProviderType.POLLINATIONS:
                    raise
                await asyncio.sleep(1.5)

    async def _stream_openai_lines(self, url, payload, d, usage):
        async with self._client.stream(
            "POST", url, json=payload, headers=self._headers(d.provider)
        ) as r:
            if r.status_code >= 400:
                body = await r.aread()
                raise ProviderError(f"HTTP {r.status_code}: {body[:200]!r}", provider=d.provider.value)
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {}).get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield {"event": "delta", "text": delta}
                if chunk.get("usage"):
                    usage = chunk["usage"]
            yield {"event": "done", "model": d.name, "provider": d.provider.value,
                   "usage": usage}

    # ------------------------------------------------------------------ images
    async def generate_image(
        self, prompt: str, model: str = "auto", *,
        negative_prompt: str = "", width: int = 1024, height: int = 1024, steps: int = 4,
    ) -> dict[str, Any]:
        if model == "auto":
            for candidate in IMAGE_PREFERENCE:
                d = UNIVERSAL_REGISTRY.get(candidate)
                if d and await self.is_available(d):
                    model = d.name
                    break
            else:
                raise ProviderNotConfigured(
                    "No image model is available. Add a Hugging Face key or start a local "
                    "SD WebUI / ComfyUI instance.",
                    provider="none",
                )
        definition = self.resolve(model)
        if definition.category != "image":
            raise BadRequest(f"Model '{model}' does not generate images.")
        started = time.perf_counter()
        errors: list[str] = []
        # Try the requested model first, then fall back to any other available image model.
        chain = [definition.name] + [m for m in IMAGE_PREFERENCE if m != definition.name]
        for name in chain:
            candidate = UNIVERSAL_REGISTRY.get(name)
            if not candidate or candidate.category != "image":
                continue
            if not await self.is_available(candidate):
                if name == definition.name:
                    errors.append(f"{name}: not configured (missing key or local service)")
                continue
            try:
                return await self._generate_with(candidate, prompt, negative_prompt, width, height, steps, started)
            except (ProviderError, ProviderNotConfigured) as exc:
                errors.append(f"{name}: {getattr(exc, 'message', str(exc))}")
        raise ProviderError(
            "image generation failed on every available model — " + " | ".join(errors),
            provider=definition.provider.value,
        )

    async def _generate_with(self, definition, prompt, negative_prompt, width, height, steps, started):
        if definition.provider is ProviderType.STABLE_DIFFUSION:
            images = await self._sd_generate(prompt, negative_prompt, width, height, steps)
        elif definition.provider is ProviderType.POLLINATIONS:
            images = await self._pollinations_image(prompt, width, height)
        else:
            images = await self._hf_generate(definition, prompt, negative_prompt, width, height, steps)
        for image in images:
            if image.get("b64"):
                image["url"] = save_b64(image.pop("b64"), image.get("mime", "image/png"))
        return {
            "model": definition.name,
            "provider": definition.provider.value,
            "category": "image",
            "images": images,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    async def _hf_generate(self, d, prompt, negative_prompt, width, height, steps):
        key = self.key_for(ProviderType.HUGGINGFACE)
        url = f"https://router.huggingface.co/hf-inference/models/{d.model_id}"
        payload = {
            "inputs": prompt,
            "parameters": {
                "negative_prompt": negative_prompt,
                "width": max(256, width - width % 8),
                "height": max(256, height - height % 8),
                "num_inference_steps": steps,
            },
        }
        try:
            r = await self._client.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
                timeout=180.0,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc}", provider="huggingface") from exc
        if r.status_code == 503:
            raise ProviderError("model is loading on Hugging Face; retry in ~20s",
                                provider="huggingface", status_code=503)
        if r.status_code >= 400:
            raise ProviderError(self._error_detail(r), provider="huggingface")
        return [{"b64": __import__("base64").b64encode(r.content).decode(), "mime": "image/png"}]

    async def _pollinations_image(self, prompt, width, height):
        url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
        params = {
            "width": width, "height": height, "nologo": "true",
            "model": "flux", "seed": int(time.time()) % 1_000_000,
        }
        try:
            r = await self._client.get(
                url, params=params, timeout=180.0, headers=self._headers(ProviderType.POLLINATIONS)
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc}", provider="pollinations") from exc
        if r.status_code == 402:
            detail = ""
            try:
                detail = r.json().get("error", {}).get("message", "")
            except ValueError:
                detail = self._error_detail(r)
            raise ProviderError(
                detail or "free image limit reached (1 image per 45s per IP); retry shortly",
                provider="pollinations", status_code=402,
            )
        if r.status_code >= 400:
            raise ProviderError(self._error_detail(r), provider="pollinations")
        content_type = r.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ProviderError(
                f"expected an image, got {content_type or 'no content-type'}: {r.text[:200]}",
                provider="pollinations",
            )
        mime = content_type.split(";")[0]
        return [{"b64": __import__("base64").b64encode(r.content).decode(), "mime": mime}]

    async def _sd_generate(self, prompt, negative_prompt, width, height, steps):
        payload = {
            "prompt": prompt, "negative_prompt": negative_prompt,
            "width": width, "height": height, "steps": max(1, min(steps, 60)),
            "sampler_name": "DPM++ 2M Karras", "cfg_scale": 6.5,
        }
        try:
            r = await self._client.post(
                f"{settings.STABLE_DIFFUSION_URL}/sdapi/v1/txt2img", json=payload, timeout=300.0
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Stable Diffusion error: {exc}", provider="stable_diffusion") from exc
        return [{"b64": b, "mime": "image/png"} for b in r.json().get("images", [])]

    # ------------------------------------------------------------- embeddings
    async def embed(self, texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
        definition = self.resolve(model)
        if definition.provider is not ProviderType.OLLAMA:
            raise BadRequest("Only local embeddings are configured in this build.")
        out: list[list[float]] = []
        for text in texts:
            try:
                r = await self._client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                    json={"model": definition.model_id, "prompt": text},
                )
                r.raise_for_status()
                out.append(r.json()["embedding"])
            except (httpx.HTTPError, KeyError) as exc:
                raise ProviderError(f"embedding failed: {exc}", provider="ollama") from exc
        return out
