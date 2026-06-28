#!/usr/bin/env python3
"""Play the Odysseus critical alarm once, to test it on your Mac.

    python scripts/test_mac_alarm.py
    python scripts/test_mac_alarm.py "Banco de produção caiu"

On macOS this plays the configured sound (afplay) and speaks the message (say).
Off macOS, or when ODYSSEUS_MAC_ALARM_ENABLED=0, it's a no-op and says so.
Tune via the ODYSSEUS_MAC_ALARM_* env vars (see .env.example).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import mac_alarm  # noqa: E402


def main() -> int:
    msg = sys.argv[1] if len(sys.argv) > 1 else "This is a test of the critical alarm."
    print(f"enabled={mac_alarm.is_enabled()}  sound={mac_alarm._sound()}  "
          f"repeat={mac_alarm._repeat()}  say={mac_alarm._say_enabled()}")
    print(f"critical labels: {sorted(mac_alarm.critical_labels())}")
    result = mac_alarm.play_blocking(msg)  # blocking so the CLI waits for the sound
    print("result:", result)
    if not result.get("played"):
        print("\n(No sound played. On macOS, check ODYSSEUS_MAC_ALARM_ENABLED and the "
              "sound path; off macOS this is expected.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
