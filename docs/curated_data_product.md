# Curated Data Product

**Project:** Group Zeta DPWH Infrastructure Pipeline

## 1. Problem statement alignment

The pipeline gives analysts a checked, reproducible dataset of DPWH infrastructure contracts for monitoring spending and progress. The curated product supports this by typing and normalising the source, keeping every row traceable (kept with warnings or quarantined with an error code), flagging regions against the official PSGC, and marking contracts that are past their scheduled end while still On-Going.

## 2. Refresh and process logic

* **Ingestion:** `extract-file` copies the BetterGov release (Hugging Face, CC0) and the PSA PSGC workbook (CC BY 4.0) into the raw layer unchanged, after checking their SHA-256.
* **Staging:** types and normalises every column, parses both date formats, turns the 1900-01-01 placeholder into null, and splits joint-venture contractors. Only structurally broken rows (missing or duplicate contract ID, unparseable numbers or dates) are quarantined.
* **Curated:** flags each region against the PSGC without dropping rows, adds `award_savings_php`, `award_to_abc_pct`, `is_delayed` and a deterministic `record_hash`, and quarantines progress values outside 0 to 100.
* **Load:** rows the database table would reject (for example a negative `project_cost`) go to the load quarantine with an error code; the rest are loaded with a hash-guarded upsert, so a rerun on unchanged data inserts and updates nothing.
* **Partitioning:** the curated contracts are also written as Parquet partitioned by start year and month; contracts without a start date go to a null partition.
* **Schedule:** the Airflow DAG `dss150p_pipeline` runs the whole sequence daily at 02:00 UTC (`0 2 * * *`) with `catchup=False`. The source is a fixed snapshot, so scheduled runs report "Unchanged" until a new revision is downloaded.

## 3. Database schema (`curated.dpwh_projects`)

| Field | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `contract_id` | VARCHAR(64) | No | DPWH contract ID, primary key |
| `project_cost` | NUMERIC | Yes | The source `budget` in PHP, `>= 0`. Its meaning is inconsistent in the source (it matches the ABC in 9.8% of rows and the award amount in 50.0%) |
| `physical_accomplishment` | NUMERIC | Yes | Progress percentage, 0 to 100 |
| `start_date` | DATE | Yes | Contract start date |
| `infra_year` | INTEGER | Yes | Programme year as published; can differ from the start year |
| `is_delayed` | BOOLEAN | No | True when the contract is On-Going, its `expiry_date` is before the snapshot date (22 January 2026) and progress is below 100 |
| `status_name` | VARCHAR(32) | No | Completed, On-Going, For Procurement, Terminated or Not Yet Started |
| `record_hash` | CHAR(64) | No | SHA-256 of the curated business columns, used to skip unchanged rows |
| `pipeline_run_id` | VARCHAR(128) | No | Run that last inserted or changed the row |
| `loaded_at_utc` | TIMESTAMPTZ | No | When that run loaded the row |

The full curated file (`dpwh_contracts.parquet`) carries more columns than the table, including award metrics, PSGC region codes, contractor counts and warning codes; see `docs/data_contract.md`.

## 4. Intended downstream use

* **Business intelligence:** connect a BI tool to `curated.dpwh_projects` for regional spending and delay dashboards.
* **Analysis:** `python -m src.cli analyze` produces descriptive statistics, charts and a delay model in `data/analytics/`. Its insights are generated from the computed numbers and describe associations in one snapshot, not causes.

## 5. Known limits

* The source is a third-party release, not an official DPWH export, and covers about 93.5% of the portal's contracts as of 30 September 2026.
* It is a snapshot from January 2026, so statuses and progress are about eight months older than the portal.
* `project_cost` is not a consistent measure of cost (see section 3).
