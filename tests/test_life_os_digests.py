"""Tests for Life-OS digests, the English SRS, DB health alerts, and the four
new scheduled actions (morning brief, fitness digest, DBA watch, English lesson).
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def history_db(monkeypatch, tmp_path):
    from src import life_os_history as hist
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_DB", str(tmp_path / "life_os.db"))
    hist.reset_engine()
    yield hist
    hist.reset_engine()


@pytest.fixture
def app_db(monkeypatch):
    from core.database import Base
    from tests.helpers.sqlite_db import make_temp_sqlite
    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    monkeypatch.setattr("core.database.SessionLocal", SessionLocal)
    yield SessionLocal
    engine.dispose()


# --------------------------------------------------------------------------- #
# Vocabulary SRS
# --------------------------------------------------------------------------- #
def test_add_vocab_idempotent_and_due(history_db):
    history_db.add_vocab("leverage", "alavancagem", owner="me", today="2026-06-26")
    history_db.add_vocab("leverage", "alavancagem", owner="me", today="2026-06-26")
    assert history_db.vocab_stats(owner="me", today="2026-06-26")["total"] == 1
    due = history_db.due_vocab(owner="me", today="2026-06-26")
    assert len(due) == 1 and due[0]["term"] == "leverage"


def test_grade_vocab_schedules_forward_and_lapses(history_db):
    history_db.add_vocab("threshold", owner="me", today="2026-06-26")
    vid = history_db.due_vocab(owner="me", today="2026-06-26")[0]["id"]
    r1 = history_db.grade_vocab(vid, 5, owner="me", today="2026-06-26")
    assert r1["interval_days"] == 1 and r1["next_review"] == "2026-06-27"
    r2 = history_db.grade_vocab(vid, 5, owner="me", today="2026-06-27")
    assert r2["interval_days"] == 6  # reps==2 → 6 days
    # A bad grade lapses the card back to interval 1.
    r3 = history_db.grade_vocab(vid, 1, owner="me", today="2026-07-03")
    assert r3["interval_days"] == 1


def test_vocab_owner_scoped(history_db):
    history_db.add_vocab("a", owner="alice", today="2026-06-26")
    history_db.add_vocab("b", owner="bob", today="2026-06-26")
    assert history_db.vocab_stats(owner="alice")["total"] == 1
    assert history_db.vocab_stats(owner="bob")["total"] == 1


# --------------------------------------------------------------------------- #
# Fitness digest
# --------------------------------------------------------------------------- #
def _seed_weights(hist, owner, points):
    now = datetime.utcnow()
    rows = [{"external_id": f"w{i}", "metric_type": "body_mass", "value": v,
             "ts": now - timedelta(days=d)} for i, (d, v) in enumerate(points)]
    hist.upsert_health_metrics(rows, owner=owner)


def test_fitness_digest_on_target(history_db):
    from src import life_os_digests as dig
    # prev week ~82.8, this week ~82.2 → ~0.7%/week loss.
    _seed_weights(history_db, "me", [(10, 82.8), (9, 82.9), (3, 82.2), (1, 82.1)])
    d = dig.fitness_digest(owner="me")
    assert "No alvo" in d["markdown"] or "no alvo" in d["markdown"].lower()
    assert d["alert"] is False


def test_fitness_digest_plateau_alerts(history_db):
    from src import life_os_digests as dig
    _seed_weights(history_db, "me", [(10, 82.5), (9, 82.5), (3, 82.5), (1, 82.5)])
    d = dig.fitness_digest(owner="me")
    assert d["alert"] is True
    assert "Plat" in d["markdown"] or "estagn" in d["markdown"].lower()


def test_fitness_digest_no_data(history_db):
    from src import life_os_digests as dig
    d = dig.fitness_digest(owner="nobody")
    assert d["alert"] is False
    assert "Sem dados" in d["markdown"]


# --------------------------------------------------------------------------- #
# Morning brief + English lesson builders
# --------------------------------------------------------------------------- #
def test_morning_brief_sections(history_db, app_db):
    from src import life_os_digests as dig
    b = dig.morning_brief(owner="me")
    for section in ("Agenda", "Fitness", "Inglês", "Meta"):
        assert section in b["markdown"]


def test_english_lesson_lists_due(history_db):
    from src import life_os_digests as dig
    history_db.add_vocab("equity", "patrimônio", example="shareholder equity",
                         owner="me", today="2026-06-26")
    les = dig.english_lesson(owner="me")
    assert "equity" in les["markdown"]
    assert les["due_count"] == 1


# --------------------------------------------------------------------------- #
# DB health alerts / DBA watch
# --------------------------------------------------------------------------- #
def test_health_alerts_clean_sqlite(monkeypatch):
    from src import db_mcp
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    from sqlalchemy import create_engine, text
    eng = create_engine(f"sqlite:///{tmp.name}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER)"))
    eng.dispose()
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": [
        {"id": "s", "type": "sql", "url": f"sqlite:///{tmp.name}", "allow_write": False}]}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
    db_mcp.reset_caches()
    assert db_mcp.health_alerts("s") == []  # healthy → no alerts


def test_dba_health_scan_no_alerts(monkeypatch, history_db):
    from src import db_mcp, life_os_digests as dig
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": []}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
    db_mcp.reset_caches()
    scan = dig.dba_health_scan(owner="me")
    assert scan["alerts"] == []


# --------------------------------------------------------------------------- #
# Scheduled actions
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_morning_brief_action_pushes_note(app_db, history_db):
    from core.database import Note
    from src.builtin_actions import action_life_os_morning_brief, TaskNoop
    msg, ok = await action_life_os_morning_brief(owner="me")
    assert ok
    db = app_db()
    try:
        n = db.query(Note).filter(Note.source == "life_os_brief").one()
        assert n.due_date  # due now → reminder pipeline pushes it
    finally:
        db.close()
    with pytest.raises(TaskNoop):  # idempotent same day
        await action_life_os_morning_brief(owner="me")


@pytest.mark.asyncio
async def test_fitness_digest_action_pushes(app_db, history_db):
    from core.database import Note
    from src.builtin_actions import action_life_os_fitness_digest
    _seed_weights(history_db, "me", [(10, 82.8), (1, 82.2)])
    msg, ok = await action_life_os_fitness_digest(owner="me")
    assert ok
    db = app_db()
    try:
        assert db.query(Note).filter(Note.source == "life_os_fitness").count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_dba_watch_noop_when_healthy(app_db, history_db, monkeypatch):
    from src import db_mcp
    from src.builtin_actions import action_life_os_dba_watch, TaskNoop
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS", json.dumps({"connections": []}))
    monkeypatch.setenv("ODYSSEUS_DB_CONNECTIONS_FILE", "/nonexistent")
    db_mcp.reset_caches()
    with pytest.raises(TaskNoop):
        await action_life_os_dba_watch(owner="me")


@pytest.mark.asyncio
async def test_english_lesson_action_pushes(app_db, history_db):
    from core.database import Note
    from src.builtin_actions import action_life_os_english_lesson
    history_db.add_vocab("dividend", "dividendo", owner="me")
    msg, ok = await action_life_os_english_lesson(owner="me")
    assert ok
    db = app_db()
    try:
        assert db.query(Note).filter(Note.source == "life_os_english").count() == 1
    finally:
        db.close()


def test_new_actions_registered():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS
    for a in ("life_os_morning_brief", "life_os_fitness_digest",
              "life_os_dba_watch", "life_os_english_lesson"):
        assert a in BUILTIN_ACTIONS and a in BUILTIN_ACTION_INFO
        assert HOUSEKEEPING_DEFAULTS[a].get("ship_paused") is True
