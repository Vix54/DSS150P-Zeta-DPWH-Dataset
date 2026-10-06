# Database Entity Relationship Diagram

**Project:** Group Zeta DPWH Infrastructure Pipeline

The PostgreSQL schema created by `sql/init/01_schema.sql`. `curated.dpwh_projects` holds one row per loaded contract; `audit.partition_loads` holds one row per `load-partition` attempt. The two tables are not joined by a foreign key: an audit row describes a slice of contracts (by start year and month), not a single contract. Airflow keeps its own metadata in the separate `airflow` schema.

```mermaid
erDiagram
    DPWH_PROJECTS {
        varchar contract_id PK "DPWH contract ID, unique"
        numeric project_cost "Source budget in PHP, >= 0, nullable"
        numeric physical_accomplishment "Progress %, 0 to 100, nullable"
        date start_date "Contract start date, nullable"
        integer infra_year "Programme year, nullable"
        boolean is_delayed "On-Going, past expiry date as of 2026-01-22, progress below 100"
        varchar status_name "Contract status, not null"
        char record_hash "SHA-256 of the curated business columns, not null"
        varchar pipeline_run_id "Run that last inserted or changed the row"
        timestamptz loaded_at_utc "When that run loaded the row"
    }
    PARTITION_LOADS {
        bigserial load_id PK "One row per load-partition attempt"
        varchar pipeline_run_id "Run identity, e.g. airflow__20261007T020000"
        varchar curated_run_id "Curated run the slice came from"
        text partition_path "e.g. start_year=2023/start_month=5"
        integer load_year "Slice start year"
        integer load_month "Slice start month, null for a whole year"
        integer rows_read "Rows in the slice"
        integer records_inserted "New rows"
        integer records_updated "Rows whose record_hash changed"
        integer records_quarantined "Rows sent to the load quarantine"
        varchar status "success or failed"
        text error_message "Failure reason, null on success"
        timestamptz started_at_utc "Start time"
        timestamptz finished_at_utc "Finish time"
    }
    PARTITION_LOADS }o..o{ DPWH_PROJECTS : "loads a slice of (by start_date year and month)"
```

Column constraints and the load quarantine codes are listed in `docs/data_contract.md` (Database load).
