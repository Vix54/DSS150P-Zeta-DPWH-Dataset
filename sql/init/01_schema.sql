CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS airflow;

CREATE TABLE IF NOT EXISTS curated.dpwh_projects (
    contract_id VARCHAR(64) PRIMARY KEY,
    project_cost NUMERIC CHECK (project_cost >= 0),
    physical_accomplishment NUMERIC CHECK (physical_accomplishment >= 0 AND physical_accomplishment <= 100),
    start_date DATE,
    infra_year INTEGER,
    is_delayed BOOLEAN NOT NULL,
    status_name VARCHAR(32) NOT NULL,
    record_hash CHAR(64) NOT NULL CHECK (record_hash ~ '^[0-9a-f]{64}$'),
    pipeline_run_id VARCHAR(128) NOT NULL,
    loaded_at_utc TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS dpwh_projects_status_idx ON curated.dpwh_projects (status_name);
CREATE INDEX IF NOT EXISTS dpwh_projects_start_date_idx ON curated.dpwh_projects (start_date);

CREATE TABLE IF NOT EXISTS audit.partition_loads (
    load_id BIGSERIAL PRIMARY KEY,
    pipeline_run_id VARCHAR(128) NOT NULL,
    curated_run_id VARCHAR(64) NOT NULL,
    partition_path TEXT NOT NULL,
    load_year INTEGER NOT NULL CHECK (load_year BETWEEN 1900 AND 2100),
    load_month INTEGER CHECK (load_month BETWEEN 1 AND 12),
    rows_read INTEGER NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    records_inserted INTEGER NOT NULL DEFAULT 0 CHECK (records_inserted >= 0),
    records_updated INTEGER NOT NULL DEFAULT 0 CHECK (records_updated >= 0),
    records_quarantined INTEGER NOT NULL DEFAULT 0 CHECK (records_quarantined >= 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('success', 'failed')),
    error_message TEXT,
    started_at_utc TIMESTAMPTZ NOT NULL,
    finished_at_utc TIMESTAMPTZ NOT NULL
);
