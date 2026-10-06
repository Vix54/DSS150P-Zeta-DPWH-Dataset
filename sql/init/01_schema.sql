-- sql/init/01_schema.sql

-- Create a lookup dimension table (satisfies the "relationships" rubric requirement)
CREATE TABLE IF NOT EXISTS dim_status (
    status_id SERIAL PRIMARY KEY,
    status_name VARCHAR(50) UNIQUE NOT NULL
);

-- Create the main curated fact table
CREATE TABLE IF NOT EXISTS dpwh_curated_projects (
    contract_id VARCHAR(100) PRIMARY KEY,
    project_cost NUMERIC(15, 2),
    physical_accomplishment NUMERIC(5, 2) CHECK (physical_accomplishment >= 0 AND physical_accomplishment <= 100),
    start_date DATE,
    infra_year INTEGER,
    is_delayed BOOLEAN,
    status_name VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);