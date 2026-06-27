"""Tests for the Life-OS history store and collectors."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def history_db(monkeypatch, tmp_path):
    """Isolated Life-OS history DB per test."""
    from src import life_os_history as hist
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_DB", str(tmp_path / "life_os.db"))
    hist.reset_engine()
    yield hist
    hist.reset_engine()


# --------------------------------------------------------------------------- #
# History store
# --------------------------------------------------------------------------- #
def test_health_upsert_is_idempotent(history_db):
    rows = [{"external_id": "m1", "metric_type": "weight", "value": 80.0, "unit": "kg",
             "ts": datetime(2026, 6, 1, tzinfo=timezone.utc)}]
    r1 = history_db.upsert_health_metrics(rows, owner="me")
    assert r1["inserted"] == 1
    rows[0]["value"] = 79.5
    r2 = history_db.upsert_health_metrics(rows, owner="me")
    assert r2["inserted"] == 0 and r2["updated"] == 1
    s = history_db.summary(owner="me")
    assert s["health_metrics"] == 1


def test_health_upsert_hashes_id_when_missing(history_db):
    rows = [{"metric_type": "steps", "value": 10000,
             "ts": datetime(2026, 6, 2, tzinfo=timezone.utc)}]
    history_db.upsert_health_metrics(rows, owner=None)
    # Same logical row again → dedup via stable hash, not a duplicate.
    history_db.upsert_health_metrics(rows, owner=None)
    assert history_db.summary()["health_metrics"] == 1


def test_goal_log_and_mark(history_db):
    goals = [{"area": "Fitness", "goal": "treinar"}, {"area": "Inglês", "goal": "drill"}]
    r = history_db.log_goals("2026-06-26", goals, owner="me")
    assert r["created"] == 2
    # Re-seeding the same day adds nothing.
    assert history_db.log_goals("2026-06-26", goals, owner="me")["created"] == 0
    assert history_db.mark_goal("2026-06-26", "Fitness", "treinar", done=True, owner="me")

    session = history_db.get_session()
    try:
        row = session.query(history_db.GoalLog).filter(
            history_db.GoalLog.area == "Fitness").one()
        assert row.done is True
    finally:
        session.close()


def test_register_db_connections(history_db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_URL",
                       "postgresql+psycopg://u:p@localhost:5432/health")
    reg = history_db.register_db_connections()
    assert "life_os" in reg["connections"]
    assert "health_pg" in reg["connections"]
    import json
    data = json.loads(Path(reg["path"]).read_text())
    ids = {c["id"]: c for c in data["connections"]}
    assert ids["life_os"]["allow_write"] is False
    assert ids["life_os"]["url"].startswith("sqlite:///")


# --------------------------------------------------------------------------- #
# Collectors
# --------------------------------------------------------------------------- #
def test_ingest_health_skipped_without_url(history_db, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_URL", raising=False)
    from src import life_os_sources
    assert life_os_sources.ingest_health()["status"] == "skipped"


def test_ingest_health_from_sql_source(history_db, monkeypatch, tmp_path):
    """Use a temp SQLite DB as the 'external' health source (SQLAlchemy is
    engine-agnostic, so this exercises the exact prod code path)."""
    from sqlalchemy import create_engine, text
    src_path = tmp_path / "health_src.db"
    eng = create_engine(f"sqlite:///{src_path}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE hm (external_id TEXT, ts TEXT, metric_type TEXT, value REAL, unit TEXT)"))
        c.execute(text("INSERT INTO hm VALUES ('a','2026-06-01T08:00','weight',80.0,'kg'),"
                       "('b','2026-06-02T08:00','weight',79.5,'kg')"))
        c.execute(text("CREATE TABLE wo (external_id TEXT, start_ts TEXT, workout_type TEXT, energy_kcal REAL)"))
        c.execute(text("INSERT INTO wo VALUES ('w1','2026-06-01T18:00','strength',450)"))
    eng.dispose()

    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_URL", f"sqlite:///{src_path}")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", "SELECT * FROM hm")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_WORKOUTS_QUERY", "SELECT * FROM wo")

    from src import life_os_sources
    out = life_os_sources.ingest_health(owner="me")
    assert out["status"] == "ok"
    assert out["metrics"]["inserted"] == 2
    assert out["workouts"]["inserted"] == 1
    assert history_db.summary(owner="me")["health_metrics"] == 2


def test_default_health_queries_expose_expected_aliases():
    from src import life_os_sources as src
    for q in (src.DEFAULT_HEALTH_METRICS_QUERY, src.DEFAULT_HEALTH_WORKOUTS_QUERY):
        assert "external_id" in q
    assert "metric_type" in src.DEFAULT_HEALTH_METRICS_QUERY
    assert "ts" in src.DEFAULT_HEALTH_METRICS_QUERY
    assert "start_ts" in src.DEFAULT_HEALTH_WORKOUTS_QUERY
    assert "workout_type" in src.DEFAULT_HEALTH_WORKOUTS_QUERY


def _build_metrics_query(src):
    return src._build_query(
        src._METRICS_SELECT, "recorded_at",
        "ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY",
        src.hist.HealthMetric, src.hist.HealthMetric.ts)


def test_window_query_respects_env(history_db, monkeypatch):
    from src import life_os_sources as src
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_MODE", raising=False)
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", raising=False)
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_WINDOW_DAYS", "15")
    q = _build_metrics_query(src)
    assert "interval '15 days'" in q
    assert "recorded_at" in q


def test_window_default_is_30(history_db, monkeypatch):
    from src import life_os_sources as src
    for v in ("ODYSSEUS_LIFE_OS_HEALTH_MODE", "ODYSSEUS_LIFE_OS_HEALTH_WINDOW_DAYS",
              "ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY"):
        monkeypatch.delenv(v, raising=False)
    assert "interval '30 days'" in _build_metrics_query(src)


def test_watermark_mode_uses_history_max(history_db, monkeypatch):
    from datetime import datetime, timezone
    from src import life_os_sources as src
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", raising=False)
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_MODE", "watermark")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_WATERMARK_LAG_DAYS", "0")
    history_db.upsert_health_metrics(
        [{"external_id": "x", "metric_type": "weight", "value": 80.0,
          "ts": datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)}], owner="me")
    q = _build_metrics_query(src)
    # No window filter; filters on the stored max ts (lag 0).
    assert "now() - interval" not in q
    assert "recorded_at > '2026-06-20" in q


def test_watermark_first_run_is_bounded(history_db, monkeypatch):
    from src import life_os_sources as src
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", raising=False)
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_MODE", "watermark")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_WINDOW_DAYS", "10")
    q = _build_metrics_query(src)  # empty history → now - window, not unbounded
    assert "recorded_at > '" in q and "now() - interval" not in q


def test_custom_query_watermark_placeholder(history_db, monkeypatch):
    from src import life_os_sources as src
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_MODE", "watermark")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY",
                       "SELECT * FROM hm WHERE ts > '{watermark}'")
    q = _build_metrics_query(src)
    assert "{watermark}" not in q
    assert "ts > '" in q


def test_ingest_study_from_folder(history_db, monkeypatch, tmp_path):
    study = tmp_path / "study"
    (study / "sub").mkdir(parents=True)
    (study / "postgres-tuning.md").write_text("# PG tuning\nnotes")
    (study / "sub" / "english-review.md").write_text("review of phrasal verbs")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_STUDY_DIR", str(study))

    from src import life_os_sources
    out = life_os_sources.ingest_study(owner="me")
    assert out["status"] == "ok"
    assert out["study"]["inserted"] == 2

    session = history_db.get_session()
    try:
        kinds = {r.kind for r in session.query(history_db.StudyLog).all()}
        assert "review" in kinds and "study" in kinds
    finally:
        session.close()
