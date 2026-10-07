# Final Technical Report: DPWH Infrastructure Analytics & Data Engineering Pipeline

## 1. Problem and Objectives
Public infrastructure spending transparency is critical for accountability and effective resource allocation. The Department of Public Works and Highways (DPWH) manages thousands of capital projects nationwide. However, raw data releases often suffer from structural inconsistencies, missing milestone markers, and opaque status tracking. 
* **Core Problem:** Identifying what factors drive project delays, cost overruns, and stalled physical accomplishments across national infrastructure projects.
* **Primary Objectives:** 
  1. Build a robust, fault-tolerant data pipeline (Extract, Stage, Curate, Load, Validate) adhering to modern data engineering practices.
  2. Implement automated scheduling and tracking via Apache Airflow and Docker containerization.
  3. Fulfill advanced analytics requirements (Rubric 7.1) by coupling exploratory data analysis (EDA), descriptive statistics, and an XGBoost machine learning classification model to predict project delay risk.

## 2. Data Sources
* **Primary Source:** DPWH Transparency API (`api.transparency.dpwh.gov.ph`).
* **Resilience Trade-off & Fallback:** Live ingestion encounters strict HTTP 403 bot-protection challenges (Cloudflare web application firewalls). To ensure pipeline determinism and reproducibility, the architecture leverages a curated static parquet fallback mirror (derived from BetterGov datasets) via the `extract-file` CLI operator. This guarantees that evaluation runs never fail due to upstream perimeter defenses.

## 3. Architecture
The project follows a modular medallion-style data architecture implemented in Python, containerized with Docker, and orchestrated through Apache Airflow:
* **Storage Layers:** `data/raw/` (immutable source extracts), `data/stage/` (cleaned and typed intermediate tables), and `data/curated/` (denormalized, analysis-ready datasets).
* **Execution Interface:** A unified Command Line Interface (`src/cli.py`) governing modular sub-commands (`extract`, `stage`, `curate`, `load`, `validate`, `analyze`).

## 4. Implementation
* **Language & Core Libraries:** Developed in Python 3.10+ utilizing `pandas` for vectorized transformations, `xgboost` and `scikit-learn` for predictive analytics, `SQLAlchemy`/`psycopg2` for database connectivity, and `protego` for robots.txt compliance.
* **Modular Design:** Code is strictly segregated into packages (`src/extract/`, `src/stage/`, `src/transform/`, `src/load/`, `src/utils/`, `src/analytics/`) to maintain single-responsibility principles and simplify unit testing.

## 5. Data Model
* **Storage Formats:** Parquet is utilized for analytical pipeline steps due to its columnar compression and schema preservation.
* **Relational Schema:** Loaded into PostgreSQL utilizing normalized structures paired with strict constraints (primary keys, foreign keys, and hash-guarded upsert columns) to prevent duplicate record insertion during retries. Partitioning strategies organize rows by year and month to optimize query execution speeds.

## 6. Transformations and Cleaning
* **Data Cleansing:** Handling null values in critical financial and spatial attributes, parsing inconsistent date formats into standardized ISO timestamps, and deriving feature columns such as `is_delayed` flags and logarithmic cost metrics (`log_cost`) to mitigate extreme right-skewness from mega-infrastructure budgets.

## 7. Validation
* **Automated Quality Checks:** Built-in validation scripts (`src/utils/validation.py`) verify dataset integrity across pipeline boundaries by cross-checking row count parity, verifying cryptographic file hashes, and testing database match consistency between parquet files and PostgreSQL tables.

## 8. Orchestration
* **Apache Airflow:** Automated via containerized Docker Compose services (`docker-compose.airflow.yml`). The DAG (`dss150p_pipeline.py`) sequences execution tasks sequentially: `extract-file` $\rightarrow$ `stage` $\rightarrow$ `curate` $\rightarrow$ `load`, incorporating controlled failure toggles and task-retry parameters.

## 9. Deployment
* **Containerization:** Fully containerized utilizing multi-stage Dockerfiles. Environment configurations are securely managed via `.env` parameterization, separating secrets and contact parameters from core application logic.

## 10. Results and Analytics (Rubric 7.1 Bonus Fulfillment)
The analytics suite (`src/analytics/full_analysis.py`) successfully delivers:
* **Exploratory Data Analysis & Visualizations:** Generated distribution plots highlighting cost variances and boxplots comparing physical accomplishments against project status.
* **Descriptive Statistics:** Quantified delay rates and cost dispersions across budgetary tiers.
* **Predictive Modeling:** Trained an XGBoost classifier evaluating project parameters (`log_cost`, `physical_accomplishment`, `start_month`) to predict delay status, achieving robust classification metrics and feature importance rankings.
* **Defensible Insights:** Confirmed that physical accomplishment velocity and initial budget tiers are the strongest statistical precursors to project stagnation.

## 11. Challenges Encountered
1. **API Firewall Restrictions:** Overcoming strict Cloudflare 403 errors by establishing a dual-path extraction strategy supporting both live HTTP pulling and static file ingestion (`extract-file`).
2. **Version Control Attribution:** Harmonizing inconsistent author signatures across team member commits using interactive Git rebasing (`git rebase -i`) to ensure correct project attribution.
3. **Vim Interface Management:** Navigating terminal-based text editors during interactive git rebase sequences.

## 12. Limitations
* **Geospatial Granularity:** Some project records lack fine-grained GPS coordinate data, restricting high-resolution geospatial clustering.
* **Temporal Lag:** Upstream data updates depend on periodic agency reporting schedules rather than real-time IoT tracking.

## 13. Future Improvements
* **Automated Alerting:** Integrate Slack or email notification webhooks into Airflow failure callbacks.
* **Interactive Dashboarding:** Deploy a Streamlit or Superset web frontend directly over the PostgreSQL data warehouse for real-time stakeholder querying.