-- Recommended canonical schema for the Apple Health → Postgres bridge that
-- feeds Odysseus' Life OS history. Your Mac collector app writes into these two
-- tables; Odysseus reads them with the default read-only queries in
-- src/life_os_sources.py (DEFAULT_HEALTH_METRICS_QUERY / *_WORKOUTS_QUERY).
--
-- If your collector already uses different table/column names, either create
-- views named like this, or override the two ODYSSEUS_LIFE_OS_HEALTH_*_QUERY
-- env vars to match your schema. Odysseus only ever issues read-only queries.

CREATE TABLE IF NOT EXISTS health_metrics (
    id          BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    metric_type TEXT        NOT NULL,   -- body_mass, body_fat_percentage,
                                        -- step_count, heart_rate, active_energy,
                                        -- resting_heart_rate, sleep_hours, ...
    value       DOUBLE PRECISION,
    unit        TEXT,                   -- kg, %, count, bpm, kcal, h, ...
    source      TEXT DEFAULT 'apple_health',
    UNIQUE (recorded_at, metric_type, source)
);

CREATE INDEX IF NOT EXISTS idx_health_metrics_recorded_at
    ON health_metrics (recorded_at);
CREATE INDEX IF NOT EXISTS idx_health_metrics_type
    ON health_metrics (metric_type);

CREATE TABLE IF NOT EXISTS workouts (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL,
    ended_at     TIMESTAMPTZ,
    workout_type TEXT,                  -- strength, running, cycling, hiit, ...
    duration_s   DOUBLE PRECISION,
    energy_kcal  DOUBLE PRECISION,
    distance_m   DOUBLE PRECISION,
    source       TEXT DEFAULT 'apple_health',
    UNIQUE (started_at, workout_type, source)
);

CREATE INDEX IF NOT EXISTS idx_workouts_started_at
    ON workouts (started_at);

-- Daily rollups. Your collector keeps writing raw samples; these views deliver
-- the per-day aggregates the agents/dashboards actually trend on. Both avg and
-- sum are exposed so you pick the right one per metric (avg for body_mass /
-- heart_rate, sum for step_count / active_energy).
CREATE OR REPLACE VIEW health_metrics_daily AS
SELECT
    date_trunc('day', recorded_at) AS day,
    metric_type,
    source,
    count(*)   AS samples,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    sum(value) AS sum_value,
    max(unit)  AS unit
FROM health_metrics
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW workouts_daily AS
SELECT
    date_trunc('day', started_at) AS day,
    workout_type,
    source,
    count(*)          AS sessions,
    sum(duration_s)   AS total_duration_s,
    sum(energy_kcal)  AS total_energy_kcal,
    sum(distance_m)   AS total_distance_m
FROM workouts
GROUP BY 1, 2, 3;
