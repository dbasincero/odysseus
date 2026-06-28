"""Security/robustness tests for the Life-OS history + collectors:
owner isolation, connection-file handling, and watermark scoping.
"""
import json
import sys
from datetime import datetime, timezone
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


# --------------------------------------------------------------------------- #
# register_db_connections — must not crash or clobber existing config
# --------------------------------------------------------------------------- #
def test_register_handles_list_shaped_file(history_db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path), raising=False)
    # Pre-existing file in the bare-LIST shape the loader also accepts.
    (tmp_path / "db_connections.json").write_text(json.dumps(
        [{"id": "mine", "type": "sql", "url": "sqlite:///x.db", "allow_write": True}]))
    reg = history_db.register_db_connections()  # must not raise
    ids = {c["id"]: c for c in json.loads(
        (tmp_path / "db_connections.json").read_text())["connections"]}
    # Existing connection preserved (incl. its allow_write), life_os added.
    assert ids["mine"]["allow_write"] is True
    assert "life_os" in ids and ids["life_os"]["allow_write"] is False


def test_register_does_not_clobber_object_shaped_file(history_db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path), raising=False)
    (tmp_path / "db_connections.json").write_text(json.dumps(
        {"connections": [{"id": "prod", "type": "sql",
                          "url": "postgresql://h/db", "allow_write": False}]}))
    history_db.register_db_connections()
    ids = [c["id"] for c in json.loads(
        (tmp_path / "db_connections.json").read_text())["connections"]]
    assert "prod" in ids and "life_os" in ids


def test_register_tolerates_corrupt_file(history_db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path), raising=False)
    (tmp_path / "db_connections.json").write_text("{ not json")
    reg = history_db.register_db_connections()  # must not raise
    assert "life_os" in reg["connections"]


# --------------------------------------------------------------------------- #
# Owner isolation in the history store
# --------------------------------------------------------------------------- #
def test_history_summary_is_owner_scoped(history_db):
    history_db.upsert_health_metrics(
        [{"external_id": "a", "metric_type": "weight", "value": 80,
          "ts": datetime(2026, 6, 1, tzinfo=timezone.utc)}], owner="alice")
    history_db.upsert_health_metrics(
        [{"external_id": "b", "metric_type": "weight", "value": 90,
          "ts": datetime(2026, 6, 1, tzinfo=timezone.utc)}], owner="bob")
    assert history_db.summary(owner="alice")["health_metrics"] == 1
    assert history_db.summary(owner="bob")["health_metrics"] == 1


def test_watermark_is_owner_scoped(history_db, monkeypatch):
    from src import life_os_sources as src
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_MODE", "watermark")
    monkeypatch.setenv("ODYSSEUS_LIFE_OS_HEALTH_WATERMARK_LAG_DAYS", "0")
    monkeypatch.delenv("ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY", raising=False)
    # alice has recent data; bob has none.
    history_db.upsert_health_metrics(
        [{"external_id": "a", "metric_type": "weight", "value": 80,
          "ts": datetime(2026, 6, 20, tzinfo=timezone.utc)}], owner="alice")
    wm_alice = src._watermark(history_db.HealthMetric, history_db.HealthMetric.ts, owner="alice")
    wm_bob = src._watermark(history_db.HealthMetric, history_db.HealthMetric.ts, owner="bob")
    # alice anchors on HER stored max (proving owner scope — were it global,
    # bob would inherit alice's anchor); bob (no data) falls back to now-window.
    assert wm_alice.year == 2026 and wm_alice.month == 6 and wm_alice.day == 20
    assert wm_bob != wm_alice
    assert (wm_bob.month, wm_bob.day) != (6, 20)


def test_goal_log_owner_isolation(history_db):
    g = [{"area": "Fitness", "goal": "treino"}]
    history_db.log_goals("2026-06-26", g, owner="alice")
    history_db.log_goals("2026-06-26", g, owner="bob")
    history_db.mark_goal("2026-06-26", "Fitness", "treino", done=True, owner="alice")
    session = history_db.get_session()
    try:
        alice = session.query(history_db.GoalLog).filter(
            history_db.GoalLog.owner == "alice").one()
        bob = session.query(history_db.GoalLog).filter(
            history_db.GoalLog.owner == "bob").one()
        assert alice.done is True and bob.done is False
    finally:
        session.close()
