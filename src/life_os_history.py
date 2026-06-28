"""
life_os_history.py

Local history store for the Life-OS automation. A dedicated SQLite database
(``data/life_os.db``) that records, over time:

  - health metrics + workouts (pulled from the external Postgres your Mac app
    fills from Apple Health),
  - calendar events (snapshotted from Odysseus' CalDAV-synced calendar),
  - study / review logs (indexed from a folder), and
  - daily goal-completion history.

It uses its own SQLAlchemy engine/metadata so it never touches the main app DB.
Agents read it through the built-in **database** MCP server (register the file
as a read-only connection — see ``register_db_connections``), while the
collectors in ``src/life_os_sources.py`` are the only writers.

All ingest helpers are idempotent: rows are keyed by a stable ``external_id``
(provided by the source or hashed from the row) so re-ingesting the same data
updates in place instead of duplicating.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    UniqueConstraint, create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine = None
_Session = None
_lock = threading.Lock()


def db_path() -> str:
    override = os.getenv("ODYSSEUS_LIFE_OS_DB", "").strip()
    if override:
        return os.path.expanduser(override)
    try:
        from src.constants import DATA_DIR
    except Exception:
        DATA_DIR = os.getenv("ODYSSEUS_DATA_DIR", os.path.join(os.getcwd(), "data"))
    return os.path.join(DATA_DIR, "life_os.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stable_id(*parts: Any) -> str:
    """Deterministic id from the given parts — used when a source row carries
    no natural key, so re-ingesting the same row updates in place."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class HealthMetric(Base):
    __tablename__ = "health_metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), nullable=False)
    owner = Column(String(255), nullable=True)
    ts = Column(DateTime, nullable=True)
    metric_type = Column(String(120), nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String(40), nullable=True)
    source = Column(String(80), nullable=True)
    ingested_at = Column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("external_id", name="uq_health_external_id"),)


class Workout(Base):
    __tablename__ = "workouts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), nullable=False)
    owner = Column(String(255), nullable=True)
    start_ts = Column(DateTime, nullable=True)
    end_ts = Column(DateTime, nullable=True)
    workout_type = Column(String(120), nullable=True)
    duration_s = Column(Float, nullable=True)
    energy_kcal = Column(Float, nullable=True)
    distance_m = Column(Float, nullable=True)
    source = Column(String(80), nullable=True)
    ingested_at = Column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("external_id", name="uq_workout_external_id"),)


class CalendarSnapshot(Base):
    __tablename__ = "calendar_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), nullable=False)  # source uid
    owner = Column(String(255), nullable=True)
    calendar = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    dtstart = Column(DateTime, nullable=True)
    dtend = Column(DateTime, nullable=True)
    status = Column(String(40), nullable=True)
    ingested_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (UniqueConstraint("external_id", name="uq_cal_external_id"),)


class StudyLog(Base):
    __tablename__ = "study_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), nullable=False)
    owner = Column(String(255), nullable=True)
    ts = Column(DateTime, nullable=True)
    topic = Column(String(255), nullable=True)
    kind = Column(String(60), nullable=True)  # study | review | note
    detail = Column(Text, nullable=True)
    source = Column(String(255), nullable=True)
    ingested_at = Column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("external_id", name="uq_study_external_id"),)


class GoalLog(Base):
    __tablename__ = "goal_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    owner = Column(String(255), nullable=True)
    area = Column(String(80), nullable=False)
    goal = Column(Text, nullable=False)
    done = Column(Boolean, default=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (
        UniqueConstraint("date", "owner", "area", "goal", name="uq_goal_day"),
    )


class VocabItem(Base):
    """English vocabulary with SM-2-style spaced-repetition scheduling."""
    __tablename__ = "vocab"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String(255), nullable=True)
    term = Column(String(255), nullable=False)
    translation = Column(Text, nullable=True)
    example = Column(Text, nullable=True)
    ease = Column(Float, default=2.5)
    interval_days = Column(Integer, default=0)
    reps = Column(Integer, default=0)
    next_review = Column(String(10), nullable=True)  # YYYY-MM-DD
    added_at = Column(DateTime, default=_now)
    last_reviewed = Column(String(10), nullable=True)
    __table_args__ = (UniqueConstraint("owner", "term", name="uq_vocab_owner_term"),)


# --------------------------------------------------------------------------- #
# Engine / session
# --------------------------------------------------------------------------- #
def get_session():
    """Return a session bound to the history DB, creating it on first use."""
    global _engine, _Session
    with _lock:
        if _Session is None:
            path = db_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _engine = create_engine(
                f"sqlite:///{path}",
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(_engine)
            _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _Session()


def reset_engine() -> None:
    """Drop the cached engine (tests + after ODYSSEUS_LIFE_OS_DB changes)."""
    global _engine, _Session
    with _lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
        _engine = None
        _Session = None


# --------------------------------------------------------------------------- #
# Idempotent upserts
# --------------------------------------------------------------------------- #
def _upsert(session, model, external_id: str, values: dict) -> bool:
    """Insert or update by external_id. Returns True if newly inserted."""
    row = session.query(model).filter(model.external_id == external_id).one_or_none()
    if row is None:
        session.add(model(external_id=external_id, **values))
        return True
    for k, v in values.items():
        setattr(row, k, v)
    return False


def upsert_health_metrics(rows: list[dict], owner: Optional[str] = None) -> dict:
    session = get_session()
    inserted = updated = 0
    try:
        for r in rows:
            ext = r.get("external_id") or stable_id(
                owner, r.get("metric_type"), r.get("ts"), r.get("value"))
            vals = {
                "owner": owner,
                "ts": r.get("ts"),
                "metric_type": r.get("metric_type") or "unknown",
                "value": r.get("value"),
                "unit": r.get("unit"),
                "source": r.get("source") or "apple_health",
            }
            if _upsert(session, HealthMetric, ext, vals):
                inserted += 1
            else:
                updated += 1
        session.commit()
    finally:
        session.close()
    return {"inserted": inserted, "updated": updated}


def upsert_workouts(rows: list[dict], owner: Optional[str] = None) -> dict:
    session = get_session()
    inserted = updated = 0
    try:
        for r in rows:
            ext = r.get("external_id") or stable_id(
                owner, r.get("workout_type"), r.get("start_ts"))
            vals = {
                "owner": owner,
                "start_ts": r.get("start_ts"),
                "end_ts": r.get("end_ts"),
                "workout_type": r.get("workout_type"),
                "duration_s": r.get("duration_s"),
                "energy_kcal": r.get("energy_kcal"),
                "distance_m": r.get("distance_m"),
                "source": r.get("source") or "apple_health",
            }
            if _upsert(session, Workout, ext, vals):
                inserted += 1
            else:
                updated += 1
        session.commit()
    finally:
        session.close()
    return {"inserted": inserted, "updated": updated}


def upsert_calendar(rows: list[dict], owner: Optional[str] = None) -> dict:
    session = get_session()
    inserted = updated = 0
    try:
        for r in rows:
            ext = r.get("external_id") or stable_id(owner, r.get("summary"), r.get("dtstart"))
            vals = {
                "owner": owner,
                "calendar": r.get("calendar"),
                "summary": r.get("summary"),
                "location": r.get("location"),
                "dtstart": r.get("dtstart"),
                "dtend": r.get("dtend"),
                "status": r.get("status"),
            }
            if _upsert(session, CalendarSnapshot, ext, vals):
                inserted += 1
            else:
                updated += 1
        session.commit()
    finally:
        session.close()
    return {"inserted": inserted, "updated": updated}


def upsert_study(rows: list[dict], owner: Optional[str] = None) -> dict:
    session = get_session()
    inserted = updated = 0
    try:
        for r in rows:
            ext = r.get("external_id") or stable_id(owner, r.get("source"), r.get("ts"))
            vals = {
                "owner": owner,
                "ts": r.get("ts"),
                "topic": r.get("topic"),
                "kind": r.get("kind") or "study",
                "detail": r.get("detail"),
                "source": r.get("source"),
            }
            if _upsert(session, StudyLog, ext, vals):
                inserted += 1
            else:
                updated += 1
        session.commit()
    finally:
        session.close()
    return {"inserted": inserted, "updated": updated}


def log_goals(date: str, goals: list[dict], owner: Optional[str] = None) -> dict:
    """Seed the day's goal rows (done=False) without clobbering existing
    completion state — re-running in the same day is a no-op for done flags."""
    session = get_session()
    created = 0
    try:
        for g in goals:
            area, goal = g.get("area", "Geral"), g.get("goal", "")
            existing = session.query(GoalLog).filter(
                GoalLog.date == date, GoalLog.owner == owner,
                GoalLog.area == area, GoalLog.goal == goal,
            ).one_or_none()
            if existing is None:
                session.add(GoalLog(date=date, owner=owner, area=area, goal=goal, done=False))
                created += 1
        session.commit()
    finally:
        session.close()
    return {"created": created, "total": len(goals)}


def mark_goal(date: str, area: str, goal: str, done: bool = True,
              owner: Optional[str] = None, note: Optional[str] = None) -> bool:
    session = get_session()
    try:
        row = session.query(GoalLog).filter(
            GoalLog.date == date, GoalLog.owner == owner,
            GoalLog.area == area, GoalLog.goal == goal,
        ).one_or_none()
        if row is None:
            row = GoalLog(date=date, owner=owner, area=area, goal=goal)
            session.add(row)
        row.done = done
        if note is not None:
            row.note = note
        session.commit()
        return True
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Vocabulary SRS (spaced repetition, SM-2 flavored)
# --------------------------------------------------------------------------- #
def _today_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _add_days(date_str: str, days: int) -> str:
    from datetime import datetime as _dt, timedelta as _td
    return (_dt.strptime(date_str, "%Y-%m-%d") + _td(days=days)).strftime("%Y-%m-%d")


def add_vocab(term: str, translation: str = "", example: str = "",
              owner: Optional[str] = None, today: Optional[str] = None) -> dict:
    """Add a word (idempotent per owner+term). New words are due immediately."""
    term = (term or "").strip()
    if not term:
        return {"added": False, "reason": "empty term"}
    today = today or _today_str()
    session = get_session()
    try:
        row = session.query(VocabItem).filter(
            VocabItem.owner == owner, VocabItem.term == term).one_or_none()
        if row is None:
            session.add(VocabItem(owner=owner, term=term, translation=translation,
                                  example=example, next_review=today))
            created = True
        else:
            # Enrich without resetting schedule.
            if translation:
                row.translation = translation
            if example:
                row.example = example
            created = False
        session.commit()
        return {"added": created, "term": term}
    finally:
        session.close()


def due_vocab(owner: Optional[str] = None, today: Optional[str] = None,
              limit: int = 20) -> list[dict]:
    """Words whose next_review is today or earlier (or never reviewed)."""
    today = today or _today_str()
    session = get_session()
    try:
        q = session.query(VocabItem)
        if owner is not None:
            q = q.filter(VocabItem.owner == owner)
        q = q.filter((VocabItem.next_review == None) | (VocabItem.next_review <= today))  # noqa: E711
        rows = q.order_by(VocabItem.next_review).limit(limit).all()
        return [{"id": r.id, "term": r.term, "translation": r.translation,
                 "example": r.example, "reps": r.reps} for r in rows]
    finally:
        session.close()


def grade_vocab(vocab_id: int, quality: int, owner: Optional[str] = None,
                today: Optional[str] = None) -> dict:
    """Apply an SM-2 review. quality 0-5; <3 lapses the card to interval 1."""
    today = today or _today_str()
    quality = max(0, min(5, int(quality)))
    session = get_session()
    try:
        row = session.query(VocabItem).filter(VocabItem.id == vocab_id).one_or_none()
        if row is None or (owner is not None and row.owner != owner):
            return {"ok": False, "reason": "not found"}
        if quality < 3:
            row.reps = 0
            row.interval_days = 1
        else:
            row.reps = (row.reps or 0) + 1
            if row.reps == 1:
                row.interval_days = 1
            elif row.reps == 2:
                row.interval_days = 6
            else:
                row.interval_days = max(1, round((row.interval_days or 1) * (row.ease or 2.5)))
        row.ease = max(1.3, (row.ease or 2.5) + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        row.last_reviewed = today
        row.next_review = _add_days(today, row.interval_days)
        session.commit()
        return {"ok": True, "term": row.term, "interval_days": row.interval_days,
                "next_review": row.next_review}
    finally:
        session.close()


def vocab_stats(owner: Optional[str] = None, today: Optional[str] = None) -> dict:
    today = today or _today_str()
    session = get_session()
    try:
        q = session.query(VocabItem)
        if owner is not None:
            q = q.filter(VocabItem.owner == owner)
        total = q.count()
        due = q.filter((VocabItem.next_review == None) | (VocabItem.next_review <= today)).count()  # noqa: E711
        learned = q.filter(VocabItem.reps >= 3).count()
        return {"total": total, "due": due, "learned": learned}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Read helpers (handy for the daily-brief / agents)
# --------------------------------------------------------------------------- #
def summary(owner: Optional[str] = None) -> dict:
    session = get_session()
    try:
        def _count(model):
            q = session.query(model)
            if owner is not None:
                q = q.filter(model.owner == owner)
            return q.count()
        return {
            "health_metrics": _count(HealthMetric),
            "workouts": _count(Workout),
            "calendar_events": _count(CalendarSnapshot),
            "study_logs": _count(StudyLog),
            "goal_log": _count(GoalLog),
            "db_path": db_path(),
        }
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Register the history DB (and optional health source) with the database MCP
# server, so the DBA / Life OS personas can query it via db_query.
# --------------------------------------------------------------------------- #
def register_db_connections(include_health: bool = True) -> dict:
    """Merge a read-only ``life_os`` connection (and the health Postgres, if
    ``ODYSSEUS_LIFE_OS_HEALTH_URL`` is set) into ``data/db_connections.json``."""
    try:
        from src.constants import DATA_DIR
    except Exception:
        DATA_DIR = os.getenv("ODYSSEUS_DATA_DIR", os.path.join(os.getcwd(), "data"))
    path = os.path.join(DATA_DIR, "db_connections.json")

    conns: list[dict] = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            # The connections file is accepted as either a bare list or a
            # {"connections": [...]} object — handle both without assuming .get.
            if isinstance(data, dict):
                conns = data.get("connections", [])
            elif isinstance(data, list):
                conns = data
            else:
                conns = []
        except (OSError, ValueError):
            conns = []

    by_id = {c.get("id"): c for c in conns if isinstance(c, dict)}
    by_id["life_os"] = {
        "id": "life_os", "name": "Life OS history", "type": "sql",
        "url": f"sqlite:///{db_path()}", "allow_write": False,
    }
    health_url = os.getenv("ODYSSEUS_LIFE_OS_HEALTH_URL", "").strip()
    if include_health and health_url:
        by_id.setdefault("health_pg", {
            "id": "health_pg", "name": "Apple Health (Postgres)", "type": "sql",
            "url": health_url, "allow_write": False,
        })

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"connections": list(by_id.values())}, fh, indent=2)
    return {"path": path, "connections": list(by_id.keys())}
