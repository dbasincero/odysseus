#!/usr/bin/env python3
"""Seed the "Life OS" starter pack into this Odysseus install.

Installs the Life-OS skills into the skills library and creates the tracking
notes (goals dashboard + per-area logs). Both are idempotent, so it is safe to
run more than once — existing skills/notes are skipped.

The chat personas (DBA Sênior, Coach Fitness, Investidor, Empreendedor, Tutor
de Inglês, Família & Pessoal, Mentor Tech, Life OS) ship as built-in presets
and need no seeding — they appear in the preset selector automatically.

Usage:
    python scripts/seed_life_os.py                 # single-user (owner = none)
    python scripts/seed_life_os.py --owner you@example.com
    python scripts/seed_life_os.py --skills-only
    python scripts/seed_life_os.py --notes-only
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import life_os  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Life OS starter pack.")
    parser.add_argument("--owner", default=None,
                        help="Owner username/email (omit for single-user installs).")
    parser.add_argument("--skills-only", action="store_true", help="Seed only skills.")
    parser.add_argument("--notes-only", action="store_true", help="Seed only notes.")
    args = parser.parse_args()

    if args.skills_only and args.notes_only:
        parser.error("--skills-only and --notes-only are mutually exclusive")

    owner = args.owner

    if not args.notes_only:
        s = life_os.seed_skills(owner=owner)
        print(f"Skills: {len(s['added'])} added, {len(s['skipped'])} already present "
              f"(of {s['total']}).")
        for name in s["added"]:
            print(f"  + {name}")

    if not args.skills_only:
        n = life_os.seed_notes(owner=owner)
        print(f"Notes:  {len(n['created'])} created, {len(n['skipped'])} already present "
              f"(of {n['total']}).")
        for title in n["created"]:
            print(f"  + {title}")

    print("\nDone. Open Notes in Odysseus and pick a persona in the chat preset "
          "selector to get started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
