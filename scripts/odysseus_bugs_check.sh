#!/bin/bash
# odysseus_bugs_check.sh — post-PR security & bug verification.
#
# Runs the security/regression suites for the surfaces most prone to the bug
# classes we've hit (read-only bypass, credential leakage, owner isolation,
# idempotency, config-shape crashes), plus a fast import smoke test. Used by the
# /odysseus_bugs skill, but safe to run by hand or in CI.
#
#   ./scripts/odysseus_bugs_check.sh            # security + Life-OS/DB suites
#   ./scripts/odysseus_bugs_check.sh --full     # also run the full fast lane
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PY="python3"
[ -x "./venv/bin/python3" ] && PY="./venv/bin/python3"

FAIL=0
run() { echo; echo "▶ $*"; "$@" || FAIL=1; }

echo "== Odysseus bug/security check =="

# 1. App still imports (catches broken wiring before tests).
run "$PY" -c "import app; print('app import OK')"

# 2. Targeted security + feature suites.
run "$PY" -m pytest -q -p no:cacheprovider \
    tests/test_db_mcp_security.py \
    tests/test_db_mcp.py \
    tests/test_life_os_security.py \
    tests/test_life_os_history.py \
    tests/test_life_os_actions.py \
    tests/test_life_os_digests.py \
    tests/test_mac_alarm.py \
    tests/test_life_os.py

# 3. Read-only guard smoke check (independent of the suite).
run "$PY" - <<'PY'
from src.db_mcp import is_read_only_sql
must_block = [
    "SELECT lo_export(1,'/tmp/x')", "SELECT pg_read_file('/etc/passwd')",
    "SELECT dblink('h','SELECT 1')", "SELECT pg_sleep(60)",
    "PRAGMA user_version = 5", "SELECT 1; DROP TABLE t",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
]
must_allow = ["SELECT * FROM users", "PRAGMA integrity_check"]
bad = [q for q in must_block if is_read_only_sql(q)[0]]
bad += [q for q in must_allow if not is_read_only_sql(q)[0]]
assert not bad, f"read-only guard regressions: {bad}"
print("read-only guard OK")
PY

# 4. Optional full fast lane.
if [ "${1:-}" = "--full" ]; then
    run "$PY" -m pytest -q -p no:cacheprovider -m "not slow"
fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo "✅ All checks passed."
else
    echo "❌ Some checks FAILED — see output above."
fi
exit "$FAIL"
