# Project Handoff Documentation

**Project:** DPWH Infrastructure Data Pipeline (Group Zeta)  
**Status:** Pipeline Implementation, Validation and Airflow Containerized Execution Complete  
**Accompanying Specifications:** `docs/sources.md`, `docs/data_contract.md`, `docs/architecture_review.md`

---

## 1. Repository State and Tagged Milestones

| Repository Reference | Milestone Scope and Implementation Details |
| :--- | :--- |
| `main` (`v0.1.0`) | Milestone 1: Environment architecture, container definitions, configuration loaders (`src/config.py`), and diagnostic suite (`validate-env`). |
| `main` (`v0.2.0`) | Raw Ingestion: Polite API extractor architecture (retained for audit provenance) and verified file-based ingestion pipeline. |
| `main` (`v0.2.1`, `v0.2.2`) | Section 7 specification alignment: `extract_sources(run_id)`, acceptance transcripts, and the PSA Philippine Standard Geographic Code (PSGC) reference dataset. |
| `main` (`v0.3.0`) | Staging and Curated Layers: Processing the primary BetterGov asset, PSGC geographic validation, quarantine routing, metrics calculation, and `record_hash` generation. |
| `main` (`v0.4.0`) | Relational Loading and Optimization: Idempotent PostgreSQL upsert, temporal partitioning, partition-level loads, `validate` suite, storage benchmarks, descriptive analytics, and containerized Airflow DAG infrastructure (`docs/evidence/20`–`29`). |
| `main` (`v0.4.2`) | Airflow 2.10.5 runs in its container: full runs and a controlled-failure run that recovers on retry (`docs/evidence/30`–`31`, `docs/images/airflow_*.png`). |
| **Test Suite** | 105 passed tests, 1 expected database test skipped when scratch environment is unconfigured (`pytest -q`). |

---

## 2. Environment Bootstrap and Clean-Room Reproduction

Execute the following commands within an isolated POSIX shell (WSL or Linux) to reproduce the execution environment:

```bash
git clone https://github.com/Vix54/DSS150P-Zeta-DPWH-Dataset.git
cd DSS150P-Zeta-DPWH-Dataset
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Populate all configuration placeholders within `.env` (ensuring valid contact details for `SCRAPER_CONTACT`; `.env` must remain untracked by Git). Retrieve the primary raw dataset `dpwh_transparency_data_all_details.parquet` from the Hugging Face repository (`bettergovph/dpwh-transparency-data`, commit `648ea96`) into `data/source/`, and trigger ingestion:

```bash
python -m src.cli validate-env
python -m src.cli extract-file
```

The `extract-file` command validates the payload SHA-256 against the reference digest (`953f0bf9…a7c97`), persisting the file byte-for-byte into `data/raw/source=bettergov_hf/run_id=<UTC timestamp>/` accompanied by `manifest.jsonl` and `run.json`. Re-executing against an unchanged source file exits with no changes written.

---

## 3. Admitted Raw Data Sources and Invariants

The data pipeline admits two upstream data assets. The authoritative reference standard is the Philippine Statistics Authority (PSA) Philippine Standard Geographic Code (PSGC) as of 30 June 2026, licensed under CC BY 4.0. This asset is staged via:
```bash
python -m src.cli extract-file --source psa_psgc
```
It is ingested into `data/raw/source=psa_psgc/` to validate regional designations; provinces are not compared, because the source's `province` field holds district engineering offices *(Note: reading the raw Excel format via pandas requires `openpyxl`)*.

The primary operational dataset is the BetterGov.ph CC0 release of DPWH transparency data (revision `648ea96`, January 2026). The `_all_details` variant aggregates base project listings with contract-level procurement metadata, serving as the raw baseline for transformation.

| Dimension | Measured Parameter |
| :--- | :--- |
| **Row Count** | 248,421 records (strictly unique on `contractId`) |
| **Attribute Count** | 52 columns |
| **Portal Coverage** | 93.5% of the 265,582 aggregate projects reported by the DPWH portal on 30 September 2026 |
| **Acquisition Provenance** | Documented by upstream publisher as collected via browser fingerprint impersonation. Not gathered by the team; documented in `docs/sources.md` for project disclosure. |

To inspect raw data interactively without mutating source files:

```python
import pandas as pd
from pathlib import Path

raw_dir = sorted(Path("data/raw/source=bettergov_hf").glob("run_id=*"))[-1]
df_raw = pd.read_parquet(raw_dir / "dpwh_transparency_data_all_details.parquet")
df = df_raw.copy()
```

---

## 4. Empirical Profile of the Raw Data Asset

Systematic profiling revealed the following structural characteristics within the raw BetterGov dataset:

* **Column Redundancies:** Attributes `infraType_1`, `latitude_1`, and `longitude_1` are identical to `infraType`, `latitude`, and `longitude` across 100% of rows.
* **Budget Metrics Inconsistency:** The `budget` attribute matches `abc` in 9.8% of rows and `awardAmount` in 50.0% of rows, diverging from both in approximately 40% of records. Consequently, `budget` is not used to derive the award metrics (which use `abc` and `awardAmount`); it is carried as `project_cost` with this caveat and should not be read as a single measure of cost.
* **Payment Invariance:** The `amountPaid` attribute contains 0 across all records.
* **Data Type Divergence:** Core financial and chronological attributes (`abc`, `awardAmount`, `infraYear`) are encoded as text rather than numerical types.
* **Undocumented Categorical Statuses:** The `status_1` column contains undocumented internal codes: `F` (138,363), `A` (103,747), `CP` (3,747), `null` (1,830), `T` (498), `ANV` (186), `FNV` (45), `TNV` (3), and `C` (2).
* **Chronological Inconsistencies:** Timestamps predominantly follow ISO-8601 formatting. However, 9 records associated with regional office `25FI` utilize `MM/DD/YYYY hh:mm:ss AM/PM`. Furthermore, the placeholder date `1900-01-01` stands in for missing values across 446 advertisement dates and 186 bidding deadlines.
* **Contractor Entity Patterns:** String structures combine corporate names, entity IDs, revocation statuses, and joint ventures (`A (id) / B (id)` occurring across 11,625 rows), alongside historical operational names (`(FORMERLY ...)`).
* **Truncation Boundaries:** The source truncates contractor names at 50 characters (12,209 of the 12,818 member names with unbalanced brackets); the longest observed name reaches 100 characters.
* **Missing Awardees:** 6,808 rows contain null contractor entities, comprising all 6,800 "For Procurement" contracts, 7 "Completed" contracts, and 1 "Not Yet Started" contract.
* **Physical Accomplishment Outliers:** Three records record negative physical progress (-100%, -0.1%, and -0.02%).
* **Complex Data Types:** Attributes `components`, `bidders`, and `coordinates` contain nested lists of records.

Reconciliation against the DPWH Portal Baseline (`docs/reconciliation_baseline.md`):

| Project Status | BetterGov Release (Jan 2026) | Portal Baseline (30 Sep 2026) | Difference (Portal − Release) |
| :--- | :--- | :--- | :--- |
| **Completed** | 205,109 | 214,535 | +9,426 |
| **On-Going** | 34,730 | 30,058 | −4,672 |
| **For Procurement** | 6,800 | 19,065 | +12,265 |
| **Terminated** | 929 | 1,037 | +108 |
| **Not Yet Started** | 853 | 887 | +34 |
| **Aggregate Total** | 248,421 | 265,582 | +17,161 |

This variance aligns with an upstream snapshot roughly eight months older than the active portal baseline: active projects advanced toward completion, while newly posted tenders account for the remaining difference.

---

## 5. Staging and Curated Transformation Pipeline

Data transformations execute via CLI entry points and conform to `docs/data_contract.md`. The clean-room execution over raw run `20261006T231240Z` (evidence 22) produced the following metrics:

| Processing Stage | Execution Command | Output Summary |
| :--- | :--- | :--- |
| **Staging** | `python -m src.cli stage` | 248,421 records staged; 0 quarantined; 253,273 contractor members parsed (identifying 11,591 joint ventures post-deduplication). |
| **Curated** | `python -m src.cli curate` | 248,418 records curated; 3 quarantined (negative progress values); 248,121 records aligned with PSGC regions ("Central Office" intentionally unmapped); 23,769 contracts identified as delayed as of 22 January 2026. |

**Enforced Pipeline Rules:**
1. Staging quarantines only records exhibiting fatal structural corruption; contextual anomalies are preserved alongside explicit `W_*` warning markers to maintain reconciliation totals.
2. The alternative date formatting pattern is parsed explicitly, while `1900-01-01` values are converted to formal nulls alongside diagnostic warnings.
3. Duplicated contractor entity entries within a single contract (identified in 34 contracts) are de-duplicated prior to evaluating joint venture status.
4. Curated schemas enforce target database naming conventions (`contract_id`, `project_cost`, `physical_accomplishment`, `start_date`, `infra_year`, `is_delayed`, `status_name`), generating `record_hash` and `processed_at_utc`.
5. PSGC reference data acts solely as a validation cross-reference; it never overwrites primary data.
6. The `is_delayed` flag evaluates against `expiry_date`, as `completion_date` remains empty across all On-Going records.

---

## 6. Storage, Database Loading, Validation, and Benchmarks

| System Component | Technical State and Functionality |
| :--- | :--- |
| **PostgreSQL Database** | Initialized via `init-db`, followed by `load`: executes a hash-guarded upsert into `curated.dpwh_projects`. Rows failing table constraints are routed to `data/quarantine/load/` under `Q_LOAD_*` codes. Re-running on unchanged data returns `inserted=0, updated=0`. |
| **Data Partitioning** | Managed by `partition`, generating paths under `data/partitioned/dpwh_contracts/start_year=YYYY/start_month=M/`. Targeted loads run via `load-partition --year --month`, logging execution details to `audit.partition_loads`. |
| **Pipeline Validation** | Executed via `validate`: validates raw manifests, checksum digests, layer-to-layer row reconciliation, analytical derivations, and database hash integrity. Runs every check and exits with code 1 if any check fails. |
| **Performance Benchmarks** | Executed via `benchmark`: benchmarks CSV, JSON Lines, Parquet (Snappy, Zstandard), and PostgreSQL across write speed, file size, full scans, and filtered queries (median of 5 runs). |
| **Workflow Orchestration** | Defined in `dags/dss150p_pipeline.py` via CLI execution points. Environment encapsulated in `Dockerfile.airflow` and `docker-compose.airflow.yml` under Airflow 2.10.5. |

Execution Results on Real Data (Evidence captures 20–31):
* **Environment Validation:** Local host 25 passed with 0 failures (evidence 20); rebuilt Docker image 24 passed with 1 expected skip (evidence 29).
* **Clean-Room Build:** Clean-room rebuild confirmed identical counts: 248,421 staged, 248,418 curated, 3 quarantined, 248,121 PSGC-matched, and 23,769 delayed contracts.
* **Partition Distribution:** 125 temporal partitions generated; 7,669 contracts without valid start dates isolated in `__HIVE_DEFAULT_PARTITION__`.
* **Database Ingestion:** 248,418 records loaded initially; zero records quarantined. Secondary execution yielded `inserted=0, updated=0, unchanged=248418`.
* **Automated Validation:** 30 validation checks passed with 0 failures.
* **Partition Ingestion Verification:** Loading the May 2023 partition (`load-partition --year 2023 --month 5`) processed 3,002 rows, returning 0 inserts and 0 updates, logging an audit status of `success`.
* **Fault Recovery Test:** Deliberately corrupting a curated file caused `validate` to fail with exit code 1. Executing `curate --rebuild` restored state, validation passed, and downstream loading returned 0/0.
* **Airflow Orchestration:** In the Airflow container, full runs completed every task through the CLI (`load` reported `inserted=0, updated=0, unchanged=248418`; `validate` passed 30 checks). A partition-mode run with `controlled_failure` failed `validate` on attempt 1, retried automatically and passed on attempt 2 (evidence 30–31, screenshots `docs/images/airflow_01`–`06`).

Storage and Query Benchmarks (Median of 5 runs; 248,418 rows $\times$ 43 columns; Filter: `status_name = 'On-Going'` returning 34,728 rows):

| Format / Database Engine | Footprint (MB) | Ingestion / Write (s) | Full Read Scan (s) | Filtered Query Scan (s) |
| :--- | :--- | :--- | :--- | :--- |
| **CSV** | 219.8 MB | 7.50 s | 3.12 s | 3.26 s |
| **JSON Lines** | 415.0 MB | 9.20 s | 5.85 s | 5.75 s |
| **Parquet (Snappy)** | 49.4 MB | 0.54 s | 0.12 s | 0.10 s |
| **Parquet (Zstandard)** | 33.9 MB | 0.56 s | 0.10 s | 0.09 s |
| **PostgreSQL** | 226.1 MB | 10.98 s | 3.65 s | 0.54 s |

---

## 7. Outstanding Action Items and Transition Priorities

| Scope | Required Actions |
| :--- | :--- |
| **Secondary Validation** | Reconcile curated status totals against `docs/reconciliation_baseline.md`. Perform browser-based manual spot-checks on 30 randomly sampled contracts (`random_state=42`). Profile nested structures (`components`, `bidders`, `coordinates`) and investigate the remaining 40 winner-name edge cases. |
| **Supplemental Data Ingestion** | Ingest verified secondary datasets strictly to corroborate existing entities; secondary sources must never overwrite primary records. Admission of PhilGEPS Open Data is pending confirmation of its download availability and terms. |

---

## 8. Governance and Engineering Principles

1. **Raw Layer Preservation:** Never mutate files within `data/raw/`. Ensure untracked directories (`data/`, `.env`) remain excluded from version control.
2. **Ethical Collection Posture:** Refrain from deploying evasion techniques or bot-mitigation bypasses. All candidate data sources must clear the admission rubric in `docs/sources.md`.
3. **Commit and Branching Standards:** Follow the team commit standard:  
   `Lastname - Type of Action - project part - short description`  
   Maintain focused commits on short-lived feature branches, preserving a clean history on `main` alongside semantic milestone tags (next tags at further milestones).

---

## 9. Environment Notes

* Local development runs on Python 3.14 (WSL); the Docker image uses Python 3.11. Tests and environment checks pass on both.
* The database is published on host port 5433 in local `.env` files because another course project already uses 5432; containers always reach it on the internal port 5432.
* The schema in `sql/init/` changed in `v0.4.0`, so an existing database volume must be recreated once with `docker compose down -v` (the load rebuilds its contents).
* For Airflow, set `AIRFLOW_HOST_PORT` in `.env` if 8080 is taken (8081 was used alongside Lab 3), and `AIRFLOW_UID` to the output of `id -u` on Linux and WSL. Start it with `docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d --build`; un-pausing the DAG starts one scheduled run immediately.
