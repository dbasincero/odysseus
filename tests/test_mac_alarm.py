"""Tests for the macOS critical alarm (src/mac_alarm.py).

Logic-only: the runner is injected so no real audio plays, and platform/enable
gating is exercised without depending on the host OS.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import mac_alarm  # noqa: E402


# --------------------------------------------------------------------------- #
# Criticality detection (how the user categorizes)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,importance,expected", [
    ("critical", None, True),
    ("Crítico", None, True),
    ("CRITICO", None, True),
    (None, "critical", True),     # calendar importance
    ("work critical", None, True),  # token within label
    ("fitness", None, False),
    ("", None, False),
    (None, None, False),
    (None, "high", False),
])
def test_is_critical(label, importance, expected, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MAC_ALARM_CRITICAL_LABELS", raising=False)
    assert mac_alarm.is_critical(label, importance) is expected


def test_custom_critical_labels(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_CRITICAL_LABELS", "p0,oncall")
    assert mac_alarm.is_critical("p0") is True
    assert mac_alarm.is_critical("oncall") is True
    assert mac_alarm.is_critical("critical") is False  # replaced the default set


# --------------------------------------------------------------------------- #
# Enable gating
# --------------------------------------------------------------------------- #
def test_enable_explicit(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "1")
    assert mac_alarm.is_enabled() is True
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "0")
    assert mac_alarm.is_enabled() is False


def test_enable_auto_follows_platform(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "auto")
    monkeypatch.setattr(mac_alarm.platform, "system", lambda: "Darwin")
    assert mac_alarm.is_enabled() is True
    monkeypatch.setattr(mac_alarm.platform, "system", lambda: "Linux")
    assert mac_alarm.is_enabled() is False


# --------------------------------------------------------------------------- #
# play_blocking — with an injected runner (no real audio)
# --------------------------------------------------------------------------- #
def test_play_blocking_runs_afplay_and_say(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "1")
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_REPEAT", "3")
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_SAY", "1")
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_SOUND", "/tmp/snd.aiff")
    calls = []
    res = mac_alarm.play_blocking("prod down", runner=lambda cmd: calls.append(cmd))
    afplay = [c for c in calls if c[0] == "afplay"]
    say = [c for c in calls if c[0] == "say"]
    assert len(afplay) == 3 and afplay[0] == ["afplay", "/tmp/snd.aiff"]
    assert len(say) == 1 and "prod down" in say[0][-1]
    assert res["played"] is True and res["spoke"] is True


def test_play_blocking_disabled(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "0")
    calls = []
    res = mac_alarm.play_blocking("x", runner=lambda cmd: calls.append(cmd))
    assert res["played"] is False and calls == []


def test_play_blocking_say_off(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "1")
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_SAY", "0")
    calls = []
    mac_alarm.play_blocking("x", runner=lambda cmd: calls.append(cmd))
    assert not any(c[0] == "say" for c in calls)


def test_play_blocking_handles_missing_binary(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "1")

    def boom(cmd):
        raise FileNotFoundError("afplay")
    res = mac_alarm.play_blocking("x", runner=boom)  # must not raise
    assert res["played"] is False


def test_play_alarm_noop_off_macos(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_MAC_ALARM_ENABLED", "auto")
    monkeypatch.setattr(mac_alarm.platform, "system", lambda: "Linux")
    assert mac_alarm.play_alarm("x")["started"] is False
