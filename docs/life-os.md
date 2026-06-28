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

### Proactive digests (push to your phone)

Four more scheduled tasks turn the history into proactive nudges. They ship
**paused** — enable under **Settings → Tasks**. Each builds a Note with a
due-now reminder, so it's delivered through your configured reminder channel
(ntfy push / browser / email).

| Task | When | What it pushes |
|------|------|----------------|
| **Life OS Morning Brief** | daily 06:30 | Today's calendar, weight trend, English review count, and the #1 goal per area. |
| **Life OS Fitness Digest** | weekly (Sun 08:00) | Weight/body-fat trend, training adherence, a **calorie adjustment**, and a **plateau alert**. |
| **Life OS DBA Night Watch** | every 2h | Scans your DB connections; alerts **only** on blocked locks, long queries, or integrity issues, and appends to the runbook (`data/personal_docs/runbook/dba-watch.md`). |
| **Life OS English Lesson** | daily 07:15 | Spaced-repetition (SM-2) vocabulary review + a prompt to run with the English tutor. |

For push delivery, configure an **ntfy** reminder channel in Settings (the
`ntfy` service is in `docker-compose.yml`); without it, briefs still appear
in-app and by email if configured.

The English SRS keeps vocabulary in the history DB (`vocab` table). The tutor —
or you — adds words via `life_os_history.add_vocab(...)` and grades reviews with
`grade_vocab(id, quality 0-5)`; due cards surface in the daily lesson.

### Critical alarm on the Mac

For things that must not be missed, Odysseus can **play an alarm and speak** on
the Mac (it runs natively, so it uses macOS `afplay` + `say`). You categorize
what's critical:

- give a **Note** (or its reminder) a critical **label** — default `critical` /
  `crítico` — or
- a **calendar event** with importance `critical`.

When that reminder fires, the alarm sounds in addition to the normal reminder
channel. An agent can trigger it too — e.g. the **DBA Sênior** persona creates a
note labeled `critical` with a due time of now when it finds production down.

Test it on the Mac:

```bash
python scripts/test_mac_alarm.py "Banco de produção caiu"
```

Tune via `ODYSSEUS_MAC_ALARM_*` (sound file, repeat count, `say` on/off, voice,
and which labels count as critical) — see `.env.example`. Off macOS or when
`ODYSSEUS_MAC_ALARM_ENABLED=0`, it's a safe no-op.

### One-time setup / backfill

```bash
# Register the DB connections (life_os + health_pg) and backfill history:
python scripts/life_os_ingest.py --owner you@example.com

# Just print what's in the history DB:
python scripts/life_os_ingest.py --summary
```

### Quickstart on an Apple Silicon Mac (M4)

For a Mac that stays on 24/7, run Odysseus natively (no Docker — native gets
Metal GPU access for Cookbook):

```bash
# 1. First run: installs deps, sets up the venv, launches Odysseus.
./start-macos.sh

# 2. (Optional) Stand up the local health Postgres with the canonical schema.
docker compose -f docker-compose.life-os.yml up -d
#    Point your Apple Health collector app at it, then in .env:
#    ODYSSEUS_LIFE_OS_HEALTH_URL=postgresql+psycopg://health:health@localhost:5432/health

# 3. Life OS setup: installs the Postgres driver, registers connections, backfills.
./scripts/setup-life-os-mac.sh you@example.com

# 4. Seed personas/skills/notes.
./venv/bin/python scripts/seed_life_os.py --owner you@example.com

# 5. In Odysseus → Settings → Tasks, enable "Life OS Daily Goals" and
#    "Life OS Ingest". Done — it now runs unattended.
```

The default health queries already match
[`config/life_os_health_schema.sql`](../config/life_os_health_schema.sql), so if
your collector writes into that schema you don't need to set the `*_QUERY` env
vars at all — only `ODYSSEUS_LIFE_OS_HEALTH_URL`.

### Update strategy — how history stays correct over time

The history is **append + idempotent upsert, never full-replace**: every row is
keyed by a stable id (your source `external_id`, or a hash of
`timestamp+type+source`), so re-reading overlapping data updates in place
instead of duplicating, and old history is never re-touched.

Two ingest modes (set `ODYSSEUS_LIFE_OS_HEALTH_MODE`):

| Mode | What it does | Best for |
|------|--------------|----------|
| `window` (default) | Re-reads the last **N days** each run and upserts. Self-heals Apple samples that arrive or get edited late. | Day-aggregated data (recommended). |
| `watermark` | Fetches only rows newer than *(last ingested timestamp − lag)*. | Raw high-frequency data you don't want to re-scan. |

Tuning (env):

- `ODYSSEUS_LIFE_OS_HEALTH_WINDOW_DAYS` — window size (default **30**).
- `ODYSSEUS_LIFE_OS_HEALTH_WATERMARK_LAG_DAYS` — safety overlap so watermark
  mode still catches recent edits (default **2**).
- Custom `*_QUERY` with a `{watermark}` placeholder runs incrementally too —
  Odysseus substitutes the timestamp before executing.

**Recommended setup:** keep raw samples in your Postgres (source of truth) but
have your collector — or a `VIEW` — aggregate to **per day / per workout**, so
years of history stay small. The schema ships two ready rollups,
`health_metrics_daily` and `workouts_daily`, which the agents can query directly
for trends. Default `window` mode at 30 days is cheap on aggregated data and
auto-healing; switch to `watermark` only if you ingest raw high-frequency rows.

This registers two **read-only** connections with the [Database MCP
server](setup.md#database-mcp-server) — `life_os` (the history DB) and
`health_pg` (if `ODYSSEUS_LIFE_OS_HEALTH_URL` is set) — so the **DBA Sênior**
and **Life OS** personas can query your history and workouts directly with
`db_query` and document insights with `db_document`. Then enable the two tasks
in the Tasks UI and you're done.

