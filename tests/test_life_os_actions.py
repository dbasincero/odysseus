"""Tests for Life-OS calendar ingest and the scheduled housekeeping actions."""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    """Real core.database models on a temp SQLite DB."""
    from core.database import Base
    from tests.helpers.sqlite_db import make_temp_sqlite

    SessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)
    monkeypatch.setattr("core.database.SessionLocal", SessionLocal)
    yield SessionLocal
    engine.dispose()


@pytest.fixture
def history_db(monkeypatch, tmp_path):
    from src import life_os_history as hist
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_DB", str(tmp_path / "life_os.db"))
    hist.reset_engine()
    yield hist
    hist.reset_engine()


def test_ingest_calendar_snapshots_events(app_db, history_db):
    from core.database import CalendarCal, CalendarEvent

    db = app_db()
    try:
        cal = CalendarCal(id="cal1", owner="me", name="Pessoal")
        db.add(cal)
        db.add(CalendarEvent(
            uid="ev1", calendar_id="cal1", summary="Treino",
            dtstart=datetime.now(timezone.utc) + timedelta(days=1),
            dtend=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
            status="confirmed",
        ))
        db.commit()
    finally:
        db.close()

    from src import life_os_sources
    out = life_os_sources.ingest_calendar(owner="me")
    assert out["status"] == "ok"
    assert out["calendar"]["inserted"] == 1
    # Idempotent re-run updates in place.
    assert life_os_sources.ingest_calendar(owner="me")["calendar"]["inserted"] == 0


@pytest.mark.asyncio
async def test_daily_goals_action_creates_note_and_logs(app_db, history_db):
    from core.database import Note
    from src.builtin_actions import action_life_os_daily_goals, TaskNoop
    from src import life_os

    msg, ok = await action_life_os_daily_goals(owner="me")
    assert ok and "note created" in msg

    db = app_db()
    try:
        notes = db.query(Note).filter(Note.source == "life_os_daily").all()
        assert len(notes) == 1
        assert notes[0].note_type == "checklist"
        assert notes[0].due_date and notes[0].repeat == "daily"
    finally:
        db.close()

    assert history_db.summary(owner="me")["goal_log"] == len(life_os.DAILY_GOALS)

    # Same day again → nothing to do.
    with pytest.raises(TaskNoop):
        await action_life_os_daily_goals(owner="me")


@pytest.mark.asyncio
async def test_ingest_action_noop_when_unconfigured(app_db, history_db, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_URL", raising=False)
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_STUDY_DIR", "/nonexistent/study/dir")
    from src.builtin_actions import action_life_os_ingest, TaskNoop

    # Calendar source has no events and health/study are unconfigured → noop.
    with pytest.raises(TaskNoop):
        await action_life_os_ingest(owner="me")


@pytest.mark.asyncio
async def test_ingest_action_reports_study(app_db, history_db, monkeypatch, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    (study / "note.md").write_text("learning notes")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_STUDY_DIR", str(study))
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_URL", raising=False)

    from src.builtin_actions import action_life_os_ingest
    msg, ok = await action_life_os_ingest(owner="me")
    assert ok
    assert "study: +1" in msg


def test_actions_registered():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS
    for action in ("life_os_ingest", "life_os_daily_goals"):
        assert action in BUILTIN_ACTIONS
        assert action in BUILTIN_ACTION_INFO
        assert action in HOUSEKEEPING_DEFAULTS
        assert HOUSEKEEPING_DEFAULTS[action].get("ship_paused") is True
