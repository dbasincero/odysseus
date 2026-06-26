"""Tests for the Life-OS starter pack (src/life_os.py): skill + note seeders,
content sanity, and the companion personas in PresetManager.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import life_os  # noqa: E402


# --------------------------------------------------------------------------- #
# Content sanity
# --------------------------------------------------------------------------- #
def test_skills_are_well_formed():
    names = [s["name"] for s in life_os.SKILLS]
    assert len(names) == len(set(names)), "skill names must be unique"
    for s in life_os.SKILLS:
        assert s["name"] and s["description"]
        assert s["procedure"], f"{s['name']} needs a procedure"


def test_notes_are_well_formed():
    titles = [n["title"] for n in life_os.NOTE_TEMPLATES]
    assert len(titles) == len(set(titles)), "note titles must be unique"
    for n in life_os.NOTE_TEMPLATES:
        assert n["title"] and n["content"].strip()


def test_personas_present_in_presets():
    from src.preset_manager import PresetManager
    keys = PresetManager.DEFAULT_PRESETS
    for persona in ["dba_senior", "fitness_coach", "investidor", "empreendedor",
                    "tutor_ingles", "familia", "tech_mentor", "life_os"]:
        assert persona in keys, f"missing persona preset {persona!r}"


# --------------------------------------------------------------------------- #
# Skill seeder (real on-disk SkillsManager in a temp data dir)
# --------------------------------------------------------------------------- #
def test_seed_skills_installs_and_is_idempotent(tmp_path):
    from services.memory.skills import SkillsManager

    mgr = SkillsManager(str(tmp_path))
    first = life_os.seed_skills(skills_manager=mgr, owner=None)
    assert set(first["added"]) == {s["name"] for s in life_os.SKILLS}
    assert first["skipped"] == []

    # SKILL.md files landed on disk.
    md_files = list((tmp_path / "skills").rglob("SKILL.md"))
    assert len(md_files) >= len(life_os.SKILLS)

    # Re-seeding dedupes: nothing new is added.
    mgr2 = SkillsManager(str(tmp_path))
    second = life_os.seed_skills(skills_manager=mgr2, owner=None)
    assert second["added"] == []
    assert set(second["skipped"]) == {s["name"] for s in life_os.SKILLS}


# --------------------------------------------------------------------------- #
# Note seeder (real Note model on a temp sqlite DB)
# --------------------------------------------------------------------------- #
@pytest.fixture
def sqlite_notes(monkeypatch):
    from core.database import Base
    from tests.helpers.sqlite_db import make_temp_sqlite

    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    monkeypatch.setattr("core.database.SessionLocal", SessionLocal)
    yield SessionLocal
    engine.dispose()


def test_seed_notes_creates_and_is_idempotent(sqlite_notes):
    from core.database import Note

    first = life_os.seed_notes(owner=None)
    assert set(first["created"]) == {n["title"] for n in life_os.NOTE_TEMPLATES}

    db = sqlite_notes()
    try:
        rows = db.query(Note).filter(Note.source == life_os.NOTE_SOURCE).all()
        assert len(rows) == len(life_os.NOTE_TEMPLATES)
        assert all(r.content for r in rows)
    finally:
        db.close()

    # Re-seed: everything is skipped, no duplicates.
    second = life_os.seed_notes(owner=None)
    assert second["created"] == []
    db = sqlite_notes()
    try:
        rows = db.query(Note).filter(Note.source == life_os.NOTE_SOURCE).all()
        assert len(rows) == len(life_os.NOTE_TEMPLATES)
    finally:
        db.close()


def test_seed_notes_owner_scoped(sqlite_notes):
    from core.database import Note

    life_os.seed_notes(owner="alice")
    db = sqlite_notes()
    try:
        alice = db.query(Note).filter(Note.owner == "alice").all()
        anon = db.query(Note).filter(Note.owner.is_(None)).all()
        assert len(alice) == len(life_os.NOTE_TEMPLATES)
        assert anon == []
    finally:
        db.close()
