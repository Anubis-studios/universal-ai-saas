#!/usr/bin/env bash
# Launch the Universal AI SaaS stack (API + built-in web client) on :8000
set -euo pipefail
cd "$(dirname "$0")/backend"
[ -f .env ] || cp .env.example .env
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
