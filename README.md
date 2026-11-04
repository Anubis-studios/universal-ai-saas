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
| Skills | 2,772 catalogued skills, all executable | uses whichever text model is up |
| Games | full single-file HTML5 game from one sentence | no — keyless fallback works |

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

## Deploy

The `Dockerfile` is the deployable unit: Python 3.11-slim, non-root user, ffmpeg, `${PORT}`-aware, health check on `/health`. Every dependency is pinned and has a prebuilt manylinux cp311 wheel, so builds don't compile anything.

**Before you expose it, set `PLATFORM_API_KEY`.** With it set, every `/api/v1/*` call needs `Authorization: Bearer <key>` (401 otherwise); `/health` and the web client stay open. Empty means open, which is fine on localhost and wrong on the internet.

Render (repo has `render.yaml`):

```bash
git remote add origin git@github.com:<you>/universal-ai-saas.git && git push -u origin main
# then: New → Blueprint → pick the repo → set the secret env vars
```

Fly.io (repo has `fly.toml`, one machine, scales to zero):

```bash
flyctl launch --no-deploy --copy-config      # keep the existing fly.toml
flyctl secrets set PLATFORM_API_KEY=$(openssl rand -hex 24)
flyctl secrets set GROQ_API_KEY=...          # any providers you have
flyctl deploy
```

Anywhere with Docker:

```bash
docker build -t universal-ai-saas .
docker run -p 8000:8000 -e PLATFORM_API_KEY=<key> -e GROQ_API_KEY=<key> universal-ai-saas
```

Two caveats that are real, not hypothetical:

- **Ephemeral disk.** Generated images and video land in `backend/outputs/`, which is wiped when the container restarts. Mount a volume at `/app/outputs` for persistence.
- **Video needs a GPU.** The client is real, but ComfyUI/SVD on CPU is unusably slow. Text and image work anywhere.

Open the web client, click **Settings**, paste `PLATFORM_API_KEY`, save. The key stays in your browser's localStorage and is sent as a bearer token on every request. If you leave the server key empty (localhost), the field can stay blank.

## API

```
GET  /health
GET  /health/ready
GET  /api/v1/models                     # catalogue + availability
GET  /api/v1/models/{name}
POST /api/v1/models/{name}/run          # text | image | embedding, by model category
POST /api/v1/chat                       # {"prompt" | "messages", "model": "auto", "stream": true}
GET  /api/v1/skills                     # ?q= search, ?category=, ?limit=&offset=
GET  /api/v1/skills/categories          # category -> count
GET  /api/v1/skills/{name}              # full skill detail
POST /api/v1/skills/{name}/run          # {"params": {...}} or {"inputs": {...}}
POST /api/v1/games/generate             # {"prompt": "...", "genre": "arcade"} -> playable HTML
GET  /games/{file}                      # the generated game, ready to play
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
                         skill_db.py           SQLite FTS5 search over the skill catalogue
                         game_forge.py         prompt -> design -> validated HTML5 game
backend/app/data/        skills_index.json.gz  2,772 catalogued skills (built, not hand-written)
scripts/                 build_skills_index.py regenerates that index from upstream
                         video_engine.py       ComfyUI SVD driver
backend/app/api/v1/      universal.py endpoint module
backend/app/workflows/   svd_img2vid.json (ComfyUI graph, API format)
backend/tests/           httpx-mocked tests, no network needed
frontend/                app.js + pages/ + styles/  (dark HUD shell)
```

## Game generation

One sentence in, one playable game out — not a template with your words swapped in.

```bash
curl -s localhost:8000/api/v1/games/generate \
  -H 'content-type: application/json' \
  -d '{"prompt": "A neon speeder dodging asteroids in a collapsing wormhole", "genre": "arcade"}'
```

Two stages: the model writes a short design spec, then writes the whole game as a single
self-contained HTML file. `validate_game()` then rejects anything that is not actually a
game — it requires a doctype, a closing `</html>`, a `<canvas>`, a script, an animation
loop, input handlers, and no external asset URLs, so the result always runs offline.
Failures surface as a real error, never as a broken page.

Free providers cap output around 4 KB, which truncates most games mid-file. The forge
detects an unterminated document, asks the model only for the remainder, and splices it
on — up to three times — then validates. Rate-limited free tiers (one reply per 45 s)
are waited out rather than failed.

## Skill catalogue

2,772 skills ship with the platform, each executable through the gateway. The index is
generated, never hand-maintained:

```bash
# point at an agentic-awesome-skills checkout
python scripts/build_skills_index.py /path/to/agentic-awesome-skills-main
```

The script parses `skills/*/SKILL.md` front matter, prefers the upstream classified
`skills_index.json` for category/risk/source, and writes a gzipped JSON index. At
startup the API loads it into SQLite and builds an FTS5 table, so search is ranked
BM25 rather than substring matching, and falls back to `LIKE` where FTS5 is unavailable.
Each catalogue skill takes free-form `input`/`context` params and is executed by
injecting its instructions into the chosen model.

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
