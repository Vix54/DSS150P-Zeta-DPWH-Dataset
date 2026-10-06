-- 1. Create Schemas
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS audit;

-- 2. Create the Curated Projects Table with strict constraints
CREATE TABLE IF NOT EXISTS curated.dpwh_projects (
    contract_id VARCHAR PRIMARY KEY,
    project_cost NUMERIC NOT NULL CHECK (project_cost >= 0),
    physical_accomplishment NUMERIC CHECK (physical_accomplishment >= -100 AND physical_accomplishment <= 100),
    start_date DATE,
    infra_year INTEGER,
    is_delayed BOOLEAN,
    status_name VARCHAR NOT NULL,
    record_hash VARCHAR NOT NULL
);

-- 3. Create the Partition Loads Audit Table
CREATE TABLE IF NOT EXISTS audit.partition_loads (
    id SERIAL PRIMARY KEY,
    load_year INTEGER,
    load_month INTEGER,
    records_inserted INTEGER,
    records_updated INTEGER,
    run_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);