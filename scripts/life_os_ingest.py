#!/usr/bin/env python3
"""Run a one-off Life-OS data ingestion and/or register DB connections.

Pulls Apple health/workouts (from the external Postgres your Mac app fills),
CalDAV-synced calendar events, and study/review docs into the local Life-OS
history database (data/life_os.db). Re-runnable and idempotent.

The scheduled `life_os_ingest` housekeeping action does this automatically
(hourly) once enabled in the Tasks UI; this script is for the initial backfill
and for wiring up the database connections agents query.

Usage:
    python scripts/life_os_ingest.py                 # ingest everything
    python scripts/life_os_ingest.py --owner you@example.com
    python scripts/life_os_ingest.py --register-only # just register DB connections
    python scripts/life_os_ingest.py --summary       # print history row counts

Configuration (env): see .env.example "Life OS automation".
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import life_os_history, life_os_sources  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Life OS data ingestion.")
    p.add_argument("--owner", default=None, help="Owner (omit for single-user).")
    p.add_argument("--register-only", action="store_true",
                   help="Only register the database connections, don't ingest.")
    p.add_argument("--no-register", action="store_true",
                   help="Skip registering DB connections.")
    p.add_argument("--summary", action="store_true",
                   help="Print history row counts and exit.")
    args = p.parse_args()

    if args.summary:
        print(json.dumps(life_os_history.summary(owner=args.owner), indent=2, default=str))
        return 0

    if not args.no_register:
        reg = life_os_history.register_db_connections()
        print(f"Registered DB connections in {reg['path']}: {', '.join(reg['connections'])}")

    if args.register_only:
        return 0

    result = life_os_sources.ingest_all(owner=args.owner)
    print(json.dumps(result, indent=2, default=str))
    print("\nHistory now holds:")
    print(json.dumps(life_os_history.summary(owner=args.owner), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
