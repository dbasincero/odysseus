"""
mac_alarm.py

Play an alarm sound (and optionally speak) on the host Mac when a *critical*
reminder fires. Odysseus runs natively on the Mac, so the process can shell out
to macOS `afplay` (play a sound file) and `say` (TTS).

You categorize what's critical by giving a Note (or its reminder) a critical
**label** — by default `critical` / `crítico` — or by a calendar event whose
importance is `critical`. When such a reminder is dispatched, the alarm plays on
the Mac in addition to the normal reminder channel.

Safe everywhere: on non-macOS, or when disabled, it's a logged no-op and never
raises. Audio plays on a daemon thread so it never blocks the scheduler, and all
commands use argument lists (no shell), so a malicious title can't inject.

Config (env):
  ODYSSEUS_MAC_ALARM_ENABLED         auto (default; on only on macOS) | 1 | 0
  ODYSSEUS_MAC_ALARM_SOUND           path to an audio file (default a system sound)
  ODYSSEUS_MAC_ALARM_REPEAT          times to play the sound (default 3)
  ODYSSEUS_MAC_ALARM_SAY             1 (default) | 0 — also speak the title
  ODYSSEUS_MAC_ALARM_VOICE           `say` voice name (optional)
  ODYSSEUS_MAC_ALARM_CRITICAL_LABELS comma list (default "critical,crítico,critico")
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading

logger = logging.getLogger(__name__)

_DEFAULT_SOUND = "/System/Library/Sounds/Sosumi.aiff"
_DEFAULT_LABELS = "critical,crítico,critico"
_PLAY_TIMEOUT_S = 20


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def is_enabled() -> bool:
    raw = os.getenv("ODYSSEUS_MAC_ALARM_ENABLED", "auto").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return _is_macos()  # "auto"


def critical_labels() -> set[str]:
    raw = os.getenv("ODYSSEUS_MAC_ALARM_CRITICAL_LABELS", _DEFAULT_LABELS)
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def is_critical(label=None, importance=None) -> bool:
    """A reminder is critical if its label is in the critical set or the
    (calendar) importance is 'critical'. Case-insensitive."""
    if importance and str(importance).strip().lower() == "critical":
        return True
    if not label:
        return False
    lab = str(label).strip().lower()
    if not lab:
        return False
    crit = critical_labels()
    # exact label match, or a critical token appears as a whole word in the label
    if lab in crit:
        return True
    tokens = {t.strip() for t in lab.replace(",", " ").split()}
    return bool(tokens & crit)


def _repeat() -> int:
    try:
        return max(1, min(int(os.getenv("ODYSSEUS_MAC_ALARM_REPEAT", "3")), 20))
    except ValueError:
        return 3


def _sound() -> str:
    return os.getenv("ODYSSEUS_MAC_ALARM_SOUND", "").strip() or _DEFAULT_SOUND


def _say_enabled() -> bool:
    return os.getenv("ODYSSEUS_MAC_ALARM_SAY", "1").strip().lower() not in ("0", "false", "no", "off")


def _default_runner(cmd: list[str]) -> None:
    subprocess.run(cmd, timeout=_PLAY_TIMEOUT_S,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_blocking(message: str = "", *, runner=None) -> dict:
    """Run the alarm synchronously (afplay x repeat, then optional say).

    Separated from play_alarm so tests can drive it directly. Returns a summary;
    never raises — a missing binary/file is logged and skipped.
    """
    if not is_enabled():
        return {"played": False, "reason": "disabled"}
    if not _is_macos() and runner is None:
        # Real audio only on macOS; tests inject a runner to exercise the logic.
        return {"played": False, "reason": "not macOS"}

    run = runner or _default_runner
    sound = _sound()
    plays = 0
    for _ in range(_repeat()):
        try:
            run(["afplay", sound])
            plays += 1
        except FileNotFoundError:
            logger.warning("afplay not found — cannot play Mac alarm")
            break
        except Exception as e:
            logger.warning("Mac alarm afplay failed: %s", e)
            break

    spoke = False
    msg = (message or "").strip()
    if _say_enabled() and msg:
        say_cmd = ["say"]
        voice = os.getenv("ODYSSEUS_MAC_ALARM_VOICE", "").strip()
        if voice:
            say_cmd += ["-v", voice]
        say_cmd.append(f"Critical alert. {msg}")
        try:
            run(say_cmd)
            spoke = True
        except Exception as e:
            logger.warning("Mac alarm say failed: %s", e)

    return {"played": plays > 0, "plays": plays, "spoke": spoke, "sound": sound}


def play_alarm(message: str = "") -> dict:
    """Fire the alarm without blocking the caller (daemon thread). Returns
    immediately with {"started": bool, "reason": ...}."""
    if not is_enabled():
        return {"started": False, "reason": "disabled"}
    if not _is_macos():
        return {"started": False, "reason": "not macOS"}
    try:
        t = threading.Thread(target=play_blocking, args=(message,),
                             name="mac-alarm", daemon=True)
        t.start()
        return {"started": True}
    except Exception as e:
        logger.warning("Mac alarm thread failed to start: %s", e)
        return {"started": False, "reason": str(e)}
