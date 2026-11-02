# Universal AI SaaS

One API and one web app over free-tier, open, and self-hosted AI models. Text chat, image
generation, video generation, and agentic skills — with automatic provider fallback when a
key is missing or a rate limit is hit.

Backend: FastAPI + httpx (async end to end). Frontend: vanilla ES modules, no build step.

## What actually runs

| Capability | Providers | Key needed |
| --- | --- | --- |
| Text + streaming | Groq, Google Gemini, Mistral, OpenRouter, Together, Cerebras, Ollama | hosted ones yes, Ollama no |
| Image | Hugging Face (FLUX.1-schnell, SDXL), SD WebUI, ComfyUI | HF yes, local no |
| Video | ComfyUI + Stable Video Diffusion (img2vid) | no |
| Embeddings | Google, Hugging Face, Ollama | yes for hosted |
| Skills | web research, code gen, summarise, extract JSON, translate | uses whichever text model is up |

`GET /api/v1/models` reports `available` per model, computed from the keys and endpoints
actually configured. Anything unavailable is hidden from the model picker rather than
failing at request time.

## Run it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add whichever free-tier keys you have
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000> — the API serves the bundled frontend. Docs at `/docs`.

The app also runs with **no keys at all** if Ollama is up: `ollama pull llama3.1:8b`.

### Running on this machine

```bash
./start.sh              # API + web client on http://localhost:8000
```

This box has no systemd, and it sleeps when idle, which kills the process. After a
wake, restart with `./start.sh`. Health check: `curl -s localhost:8000/health`.

Verified live from here with no API keys: text chat (`pollinations-openai`),
streaming, and image generation (`pollinations-flux`) all return real output;
images are written to `backend/outputs/` and served from `/media/…`. Pollinations
allows one anonymous request per IP every 45 seconds — set `POLLINATIONS_API_KEY`
to remove it.

Embeddings and video need a provider: Ollama for embeddings, ComfyUI with the SVD
checkpoint for video. Both are real clients pointed at real endpoints; they return
a clear "not configured" error until something answers on that URL.

### Docker

```bash
cp backend/.env.example backend/.env
docker compose up --build                  # app only
docker compose --profile local up -d       # + Ollama, ComfyUI, SD WebUI
docker compose exec ollama ollama pull llama3.1:8b
```

## API

```
GET  /health
GET  /health/ready
GET  /api/v1/models                     # catalogue + availability
GET  /api/v1/models/{name}
POST /api/v1/models/{name}/run          # text | image | embedding, by model category
POST /api/v1/chat                       # {"prompt" | "messages", "model": "auto", "stream": true}
GET  /api/v1/skills
POST /api/v1/skills/{name}/run          # {"params": {...}} or {"inputs": {...}}
POST /api/v1/video/generate             # ComfyUI SVD workflow
GET  /api/v1/video/health
GET  /media/{file}                      # generated images and MP4s
```

```bash
curl -s localhost:8000/api/v1/chat \
  -H 'content-type: application/json' \
  -d '{"prompt": "Explain vector databases in two sentences", "model": "auto"}'
```

Streaming uses server-sent events, so `curl -N` works too.

## Layout

```
backend/app/core/        config (pydantic-settings), errors, logging
backend/app/services/    universal_gateway.py  provider adapters, fallback, streaming
                         agentic_skills.py     skill registry + executors
                         video_engine.py       ComfyUI SVD driver
backend/app/api/v1/      universal.py endpoint module
backend/app/workflows/   svd_img2vid.json (ComfyUI graph, API format)
backend/tests/           httpx-mocked tests, no network needed
frontend/                app.js + pages/ + styles/
```

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && pytest -q
```

All provider calls are mocked with `respx`, so the suite runs offline and needs no keys.

## Design notes

- **Fallback is the point.** `model: "auto"` walks the text registry in priority order and
  moves on after a 429, 5xx, timeout, or missing key, returning which provider actually
  answered.
- **No dead ends in the UI.** The model selects are populated from live availability.
- **Video is local-only on purpose.** SVD on ComfyUI costs nothing but time; there is no
  free hosted video API worth pretending exists.
- **Skills are code, not prompts.** Each skill validates its inputs and does its own work
  (fetching, parsing, retrying) around a single model call.
