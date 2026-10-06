# Final Curated Data Product Documentation
**Project:** Group Zeta DPWH Infrastructure Pipeline

## 1. Problem Statement Alignment
The objective of this pipeline is to provide a clean, reliable, and query-optimized dataset of DPWH infrastructure projects to monitor government spending and execution efficiency. The final curated product directly supports this by resolving raw data anomalies, quarantining invalid temporal records, and enforcing strict data contracts, enabling analysts to accurately assess project delays and cost distributions.

## 2. Refresh & Process Logic
* **Ingestion:** Raw data is extracted from the BetterGov HuggingFace repository and PSA geographic files.
* **Transformation:** Staging enforces data types and standardizes text. The curation layer applies business logic (e.g., calculating `is_delayed`), drops records with negative costs, and quarantines rows with null or `9999` years.
* **Idempotency:** Data is loaded into PostgreSQL using a hash-guarded upsert (`ON CONFLICT ... DO UPDATE`) tracking state via `record_hash` to prevent duplicates on rerun.
* **Partitioning:** Valid data is additionally written to local Parquet files partitioned by `infra_year` and `month` for optimized analytical reads.
* **Schedule:** Orchestrated via Apache Airflow to run `@daily`.

## 3. Curated Schema Definition
| Field | Data Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `contract_id` | VARCHAR | False | Primary unique identifier for the DPWH contract. |
| `project_cost` | NUMERIC | False | Approved budget in PHP (Constraint: >= 0). |
| `physical_accomplishment` | NUMERIC | True | Percentage of completion (Constraint: 0-100). |
| `start_date` | DATE | True | Standardized ISO 8601 start date. |
| `infra_year` | INTEGER | True | Extracted execution year. Nulls are quarantined. |
| `is_delayed` | BOOLEAN | True | Flag indicating if the project is behind schedule. |
| `status_name` | VARCHAR | False | Current project phase (e.g., Ongoing, Completed). |
| `record_hash` | VARCHAR | False | SHA-256 hash of the row for idempotency checks. |

## 4. Intended Downstream Use
* **Business Intelligence:** Connecting PostgreSQL to tools like Metabase or PowerBI for dashboarding regional infrastructure spending.
* **Machine Learning:** Utilizing the partitioned Parquet files for predictive modeling (e.g., predicting `is_delayed` likelihood based on cost and time features).