"""
life_os_sources.py

Collectors that feed the Life-OS history store (``src/life_os_history.py``):

  - **Health / workouts** — read from the external SQL database your Mac app
    fills from Apple Health (a local Postgres container, per your setup). The
    source URL and the SELECT queries are configurable, since that app's schema
    is yours, not ours. Queries are read-only.
  - **Calendar** — snapshot Odysseus' own CalDAV-synced events (so iCloud
    calendars, once synced, land in the unified history too).
  - **Study / review** — index Markdown/text files from a folder.

Every collector degrades gracefully: missing config or a missing driver is
reported, not raised, so one broken source never blocks the others.

Configuration (env):
  ODYSSEUS_LIFE_OS_HEALTH_URL            SQLAlchemy URL to the health DB
                                         (e.g. postgresql+psycopg://u:p@localhost:5432/health)
  ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY  SELECT returning columns:
                                         external_id?, ts, metric_type, value, unit?, source?
  ODYSSEUS_LIFE_OS_HEALTH_WORKOUTS_QUERY SELECT returning columns:
                                         external_id?, start_ts, end_ts?, workout_type,
                                         duration_s?, energy_kcal?, distance_m?, source?
  ODYSSEUS_LIFE_OS_STUDY_DIR             folder of study/review notes to index
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src import life_os_history as hist

logger = logging.getLogger(__name__)

_STUDY_SUFFIXES = (".md", ".markdown", ".txt")
_STUDY_MAX_BYTES = 200_000
_STUDY_DETAIL_CHARS = 1000

# Default read-only queries used when the *_QUERY env vars aren't set. They
# target the recommended canonical schema (config/life_os_health_schema.sql):
# tables health_metrics(recorded_at, metric_type, value, unit, source) and
# workouts(started_at, ended_at, workout_type, duration_s, energy_kcal,
# distance_m, source). If your Mac collector uses different names, override the
# two env vars — the column aliases below are all the ingester needs. Postgres
# syntax (now()/interval); set the env queries for other engines.
DEFAULT_HEALTH_METRICS_QUERY = (
    "SELECT id AS external_id, recorded_at AS ts, metric_type, value, unit, source "
    "FROM health_metrics "
    "WHERE recorded_at > now() - interval '90 days' "
    "ORDER BY recorded_at"
)
DEFAULT_HEALTH_WORKOUTS_QUERY = (
    "SELECT id AS external_id, started_at AS start_ts, ended_at AS end_ts, "
    "workout_type, duration_s, energy_kcal, distance_m, source "
    "FROM workouts "
    "WHERE started_at > now() - interval '180 days' "
    "ORDER BY started_at"
)


def _coerce_dt(v: Any) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Health / workouts from the external SQL source
# --------------------------------------------------------------------------- #
def _health_engine():
    url = os.getenv("ODYSSEUS_LIFE_OS_HEALTH_URL", "").strip()
    if not url:
        return None, "ODYSSEUS_LIFE_OS_HEALTH_URL not set — skipping health ingest."
    try:
        from sqlalchemy import create_engine
        return create_engine(url, pool_pre_ping=True), None
    except Exception as e:
        return None, f"could not open health DB: {e}"


def _run_source_query(engine, query: str) -> list[dict]:
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row._mapping) for row in result]


def ingest_health(owner: Optional[str] = None) -> dict:
    """Pull metrics + workouts from the external health DB into the history."""
    engine, err = _health_engine()
    if err:
        return {"status": "skipped", "reason": err}

    out: dict[str, Any] = {"status": "ok"}
    try:
        metrics_q = (os.getenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", "").strip()
                     or DEFAULT_HEALTH_METRICS_QUERY)
        rows = _run_source_query(engine, metrics_q)
        mapped = [{
            "external_id": r.get("external_id"),
            "ts": _coerce_dt(r.get("ts") or r.get("date") or r.get("timestamp")),
            "metric_type": r.get("metric_type") or r.get("type"),
            "value": r.get("value"),
            "unit": r.get("unit"),
            "source": r.get("source") or "apple_health",
        } for r in rows]
        out["metrics"] = hist.upsert_health_metrics(mapped, owner=owner)

        workouts_q = (os.getenv("ODYSSEUS_LIFE_OS_HEALTH_WORKOUTS_QUERY", "").strip()
                      or DEFAULT_HEALTH_WORKOUTS_QUERY)
        rows = _run_source_query(engine, workouts_q)
        mapped = [{
            "external_id": r.get("external_id"),
            "start_ts": _coerce_dt(r.get("start_ts") or r.get("start") or r.get("ts")),
            "end_ts": _coerce_dt(r.get("end_ts") or r.get("end")),
            "workout_type": r.get("workout_type") or r.get("type"),
            "duration_s": r.get("duration_s"),
            "energy_kcal": r.get("energy_kcal") or r.get("calories"),
            "distance_m": r.get("distance_m"),
            "source": r.get("source") or "apple_health",
        } for r in rows]
        out["workouts"] = hist.upsert_workouts(mapped, owner=owner)
    except Exception as e:
        logger.warning("Health ingest failed: %s", e)
        return {"status": "error", "reason": str(e)}
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# Calendar from Odysseus' CalDAV-synced events
# --------------------------------------------------------------------------- #
def ingest_calendar(owner: Optional[str] = None, days_back: int = 30,
                    days_ahead: int = 180) -> dict:
    """Snapshot Odysseus calendar events (already CalDAV-synced) into history."""
    try:
        from core.database import SessionLocal, CalendarEvent, CalendarCal
    except Exception as e:
        return {"status": "skipped", "reason": f"calendar tables unavailable: {e}"}

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)
    db = SessionLocal()
    try:
        q = (
            db.query(CalendarEvent, CalendarCal)
            .join(CalendarCal, CalendarEvent.calendar_id == CalendarCal.id)
            .filter(CalendarEvent.dtstart >= start, CalendarEvent.dtstart <= end)
        )
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        rows = []
        for ev, cal in q.all():
            rows.append({
                "external_id": hist.stable_id(getattr(cal, "owner", None), ev.uid),
                "calendar": getattr(cal, "name", None) or getattr(cal, "display_name", None),
                "summary": ev.summary,
                "location": ev.location,
                "dtstart": ev.dtstart,
                "dtend": ev.dtend,
                "status": ev.status,
            })
    except Exception as e:
        logger.warning("Calendar ingest query failed: %s", e)
        return {"status": "error", "reason": str(e)}
    finally:
        db.close()
    return {"status": "ok", "calendar": hist.upsert_calendar(rows, owner=owner)}


# --------------------------------------------------------------------------- #
# Study / review docs from a folder
# --------------------------------------------------------------------------- #
def _study_dir() -> Optional[str]:
    d = os.getenv("ODYSSEUS_LIFE_OS_STUDY_DIR", "").strip()
    if d:
        return os.path.expanduser(d)
    try:
        from src.constants import PERSONAL_DIR
        return PERSONAL_DIR
    except Exception:
        return None


def ingest_study(owner: Optional[str] = None, folder: Optional[str] = None) -> dict:
    """Index Markdown/text study & review files from a folder into history."""
    folder = folder or _study_dir()
    if not folder or not os.path.isdir(folder):
        return {"status": "skipped", "reason": f"study folder not found: {folder}"}

    rows = []
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            if not fn.lower().endswith(_STUDY_SUFFIXES):
                continue
            full = os.path.join(root, fn)
            try:
                st = os.stat(full)
                if st.st_size > _STUDY_MAX_BYTES:
                    detail = ""
                else:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        detail = fh.read(_STUDY_DETAIL_CHARS)
            except OSError:
                continue
            lower = full.lower()
            kind = "review" if ("review" in lower or "revis" in lower) else "study"
            rows.append({
                "external_id": hist.stable_id(full, int(st.st_mtime)),
                "ts": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
                "topic": os.path.splitext(fn)[0],
                "kind": kind,
                "detail": detail,
                "source": full,
            })
    return {"status": "ok", "study": hist.upsert_study(rows, owner=owner)}


def ingest_all(owner: Optional[str] = None) -> dict:
    """Run every collector. Each returns its own status; failures are isolated."""
    return {
        "health": ingest_health(owner=owner),
        "calendar": ingest_calendar(owner=owner),
        "study": ingest_study(owner=owner),
    }
