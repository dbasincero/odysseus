#!/bin/bash
# Life OS setup for macOS (Apple Silicon / M4).
#
#   ./scripts/setup-life-os-mac.sh
#
# Run AFTER ./start-macos.sh has created the venv. This installs the database
# driver the Life OS needs (psycopg for Postgres), registers the read-only DB
# connections agents query, and backfills the local history once. Safe to
# re-run. The actual hourly/daily collection is the two scheduled tasks you
# enable in the Tasks UI.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Prefer the repo venv (matches start-macos.sh); fall back to python3.
if [ -x "./venv/bin/python3" ]; then
    PY="./venv/bin/python3"
else
    echo "▶ No ./venv found. Run ./start-macos.sh first to create it." >&2
    PY="$(command -v python3 || true)"
    [ -z "$PY" ] && { echo "python3 not found" >&2; exit 1; }
fi

echo "▶ Installing the Postgres driver (psycopg) into the venv…"
# psycopg[binary] ships native arm64 wheels — no compiler needed on M4.
"$PY" -m pip install --quiet "psycopg[binary]"
# pymongo too, in case you also point a Mongo connection at it (harmless).
"$PY" -m pip install --quiet pymongo

OWNER_ARG=""
if [ -n "$1" ]; then
    OWNER_ARG="--owner $1"
fi

echo "▶ Registering DB connections and backfilling Life OS history…"
"$PY" scripts/life_os_ingest.py $OWNER_ARG || {
    echo "  (ingest reported issues — that's fine if the health DB / queries aren't configured yet)"
}

cat <<'NEXT'

✓ Life OS setup done.

Next steps:
  1. (Optional) Start the local health Postgres:
       docker compose -f docker-compose.life-os.yml up -d
     and point your Mac collector app at it.
  2. In .env, set the health DB URL so Odysseus can read it:
       ODYSSEUS_LIFE_OS_HEALTH_URL=postgresql+psycopg://health:health@localhost:5432/health
  3. In Odysseus → Settings → Tasks, enable:
       • "Life OS Daily Goals"  (daily goal reminders)
       • "Life OS Ingest"       (hourly history collection)
  4. Seed personas/skills/notes if you haven't:
       ./venv/bin/python scripts/seed_life_os.py

Docs: docs/life-os.md
NEXT
