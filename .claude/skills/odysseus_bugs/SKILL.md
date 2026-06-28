---
name: odysseus_bugs
description: Post-PR security & bug verification for Odysseus. Use after every PR (or before merging) to re-run the security/regression suites and adversarially review changed code for the bug classes this project has hit — read-only bypass, credential leakage, owner isolation, idempotency, and config-shape crashes.
---

# /odysseus_bugs — verify after each PR

Run this on the PR branch (or the working diff) before merging. Goal: catch
security regressions and the recurring bug classes early, with evidence.

## Procedure

1. **Run the automated check** (this is the gate):
   ```bash
   ./scripts/odysseus_bugs_check.sh          # targeted security + feature suites
   ./scripts/odysseus_bugs_check.sh --full   # also the full fast lane (pre-merge)
   ```
   It verifies the app imports, runs the DB-MCP + Life-OS security/regression
   suites, and smoke-checks the read-only SQL guard. Non-zero exit = stop and fix.

2. **Scope the diff** to what changed:
   ```bash
   git fetch origin && git diff --stat origin/dev...HEAD
   ```

3. **Adversarially review the changed code** against this checklist (these are
   the failure modes already found in this codebase — check each that the diff
   touches):

   - **Read-only / command safety** — any new SQL/DB path: can a *read* cause a
     write or side effect? Re-derive against `src/db_mcp.py is_read_only_sql`:
     stacked statements, data-modifying CTEs, side-effect functions
     (`lo_export`, `pg_read_file`, `dblink`, `pg_sleep`, `load_file`), `PRAGMA`
     assignments. New engine? Confirm `_apply_read_only` covers its dialect.
   - **Credential / secret leakage** — does any new error message, log, or API
     response echo a connection URL/DSN, token, or password? Must pass through
     `_redact` / `_safe_err`. Check both the userinfo and query-string forms.
   - **Owner isolation** — any new query over user data (notes, history,
     memory): is it filtered by `owner`? Could one account read/affect another's
     rows (incl. watermarks, summaries, deletes)?
   - **Idempotency** — any new ingest/seed/scheduled action: does re-running
     duplicate rows or reset state? Confirm a stable key + upsert/skip.
   - **Config-shape robustness** — any code reading a JSON/config file: does it
     assume a shape (`dict` vs `list`)? Handle both or fail closed; never crash.
   - **Resource bounds** — new queries/reads: are results capped (row limits,
     file-size caps, timeouts) so a large source can't exhaust memory/context?
   - **Write gating** — new mutating path: is it behind the right gate
     (`allow_write`, admin, owner) and reversible/logged?

4. **For anything suspicious, prove it** with a tiny repro (a `python3 -c`
   snippet or a focused test) before claiming it's a bug — and prove the fix the
   same way. Add a regression test next to the existing security suites
   (`tests/test_db_mcp_security.py`, `tests/test_life_os_security.py`).

5. **Report** concisely: what was checked, what failed, the repro, and the fix
   (or a clear "no issues found in the changed surface"). If subscribed to the
   PR, only comment when there's something actionable.

## Pitfalls

- Don't claim "secure" from reading alone — run the script and a repro.
- A read-only *keyword* guard is not enough on its own; rely on the DB-level
  read-only transaction too, and keep both in sync when adding engines.
- Scope the review to the diff; don't rubber-stamp unchanged code.

## Verification

- `./scripts/odysseus_bugs_check.sh` exits 0.
- Every new data/DB/config surface in the diff is checked against the list
  above, with a repro for any finding and a regression test for any fix.
