# Life OS — centralize your goals in Odysseus

The **Life OS** pack turns Odysseus into a single place to run every area of
your life. It is tuned for one persona — a **senior DBA** who is also a
**father and son**, a **bodybuilder on an accelerated fat-loss cut**, a **stock
investor**, an **entrepreneur**, a **tech enthusiast**, and someone **learning
English** — but the pieces are plain presets/skills/notes you can edit freely.

It has three parts:

1. **Personas** — specialized chat coaches (presets), available immediately.
2. **Skills** — reusable agent procedures for recurring workflows.
3. **Tracking notes** — a goals dashboard plus per-area logs.

---

## 1. Personas (presets)

These ship as built-in presets and show up in the chat **preset selector** with
no setup. They default to Brazilian Portuguese (the English tutor is bilingual
on purpose).

| Preset | Use it for |
|--------|------------|
| **Life OS** | The integrated view — set/review goals across all areas and turn intent into tracked action. Routes you to the right specialist. |
| **DBA Sênior** | Diagnose and fix database problems (Postgres/MySQL/Oracle/MongoDB). Pairs with the [Database MCP server](setup.md#database-mcp-server). |
| **Coach Fitness** | Accelerated fat loss while preserving muscle — macros, training, weekly adjustments. |
| **Investidor de Ações** | Stock theses, valuation, portfolio risk and decisions. *Not investment advice.* |
| **Empreendedor** | Business priorities, unit economics, the next bottleneck to unblock. |
| **Tutor de Inglês** | Daily English practice with corrections, vocabulary and mini-exercises. |
| **Família & Pessoal** | Being a present father and a good son without work eating everything. |
| **Mentor Tech** | Deep-dive learning across infra, data, AI, automation and self-hosting. |

Pick one in the chat preset dropdown; switch any time. Edit them under
**Settings → Presets** (your edits are preserved across updates).

---

## 2. Skills

Reusable procedures the agent can pull into context when relevant:

| Skill | What it does |
|-------|--------------|
| `db-incident-triage` | Read-only-first triage of a slow/locked/down database, ending in a documented root cause + fix. |
| `weekly-portfolio-review` | A disciplined weekly pass over holdings, theses and risk. |
| `fat-loss-weekly-adjust` | Adjust the cut from the weekly weight/measure trend, protecting muscle. |
| `daily-english-drill` | A 10–15 min daily English loop with vocabulary logging and a streak. |
| `weekly-life-review` | Integrated weekly review across every area, ending in one next action per area. |

---

## 3. Tracking notes

Created as normal Odysseus **Notes** (searchable, editable in the UI):

- **Life OS — Painel de Metas** — the goals dashboard + weekly check-in.
- **Treino & Recomposição** and **Nutrição** — fitness logs.
- **Portfólio de Ações** — positions and a decision journal.
- **Empresa — Metas & KPIs** — business KPIs and weekly priorities.
- **Inglês — Plano de Estudo** — vocabulary, grammar notes and a streak.
- **Família & Pessoal** — rituals, commitments and important dates.

---

## Setup

The **personas need no setup**. To install the **skills and notes**, run the
idempotent seeder once:

```bash
# Single-user install
python scripts/seed_life_os.py

# Multi-user: scope to your account
python scripts/seed_life_os.py --owner you@example.com

# Only one half, if you prefer
python scripts/seed_life_os.py --skills-only
python scripts/seed_life_os.py --notes-only
```

Re-running is safe: existing skills dedupe and existing notes are skipped.

### Recommended weekly rhythm

1. Open **Life OS** and run the `weekly-life-review` skill (e.g. Sunday).
2. Update the **Painel de Metas** and pick one next action per area.
3. During the week, use each persona + skill (log workouts/meals, review the
   portfolio, do the English drill, triage any DB incident).
4. Let the DBA persona document incidents with `db_document`.

> Tip: combine with the [Database MCP server](setup.md#database-mcp-server) so
> the DBA persona can actually diagnose your databases and write findings back
> as notes.

---

## Automation — daily reminders + history collection

For a Mac that stays on 24/7, two **scheduled housekeeping tasks** turn the
Life OS into a hands-off system. Both ship **paused** — enable them under
**Settings → Tasks**.

### Daily goal reminders

The **Life OS Daily Goals** task (runs each morning, `0 7 * * *`):

- creates today's **goal checklist note** (`Metas de Hoje — YYYY-MM-DD`) with one
  item per area and an **evening reminder** (default 20:00, set
  `ODYSSEUS_LIFE_OS_GOALS_TIME`), and
- logs the day's goals to the history DB (`goal_log`) so you can track streaks.

Daily targets live in `src/life_os.py` (`DAILY_GOALS`) — edit them to taste.

### History collection

The **Life OS Ingest** task (hourly, `0 * * * *`) collects everything into a
local SQLite history database, `data/life_os.db`:

| Source | Where it comes from |
|--------|---------------------|
| Health metrics + workouts | A **local Postgres** your Mac app fills from Apple Health. Odysseus reads it via configurable read-only SELECTs. |
| Calendar events | Odysseus' own **CalDAV-synced** calendar (configure iCloud CalDAV in Calendar settings). |
| Study / review docs | A folder of Markdown/text notes (`ODYSSEUS_LIFE_OS_STUDY_DIR`, default `data/personal_docs`). |

All ingests are **idempotent** — rows are keyed by a stable id, so re-running
updates in place instead of duplicating.

#### Apple Health → Postgres → Odysseus

Apple Health has no API on macOS, so the data path is: your Mac app writes Apple
Health data into a local Postgres container, and Odysseus reads from it. Point
Odysseus at that DB and give it read-only queries (your schema, your queries):

```bash
ODYSSEUS_LIFE_OS_HEALTH_URL="postgresql+psycopg://user:pass@localhost:5432/health"
ODYSSEUS_LIFE_OS_HEALTH_METRICS_QUERY="SELECT id AS external_id, recorded_at AS ts, type AS metric_type, value, unit FROM health_metrics WHERE recorded_at > now() - interval '30 days'"
ODYSSEUS_LIFE_OS_HEALTH_WORKOUTS_QUERY="SELECT id AS external_id, started_at AS start_ts, ended_at AS end_ts, type AS workout_type, duration_s, energy_kcal, distance_m FROM workouts WHERE started_at > now() - interval '90 days'"
```

Expected columns (extras are ignored, `external_id` optional — a stable hash is
derived when absent):

- **metrics:** `external_id?`, `ts`, `metric_type`, `value`, `unit?`, `source?`
- **workouts:** `external_id?`, `start_ts`, `end_ts?`, `workout_type`, `duration_s?`, `energy_kcal?`, `distance_m?`, `source?`

### One-time setup / backfill

```bash
# Register the DB connections (life_os + health_pg) and backfill history:
python scripts/life_os_ingest.py --owner you@example.com

# Just print what's in the history DB:
python scripts/life_os_ingest.py --summary
```

This registers two **read-only** connections with the [Database MCP
server](setup.md#database-mcp-server) — `life_os` (the history DB) and
`health_pg` (if `ODYSSEUS_LIFE_OS_HEALTH_URL` is set) — so the **DBA Sênior**
and **Life OS** personas can query your history and workouts directly with
`db_query` and document insights with `db_document`. Then enable the two tasks
in the Tasks UI and you're done.

