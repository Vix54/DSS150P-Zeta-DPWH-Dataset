# Technical Report: DPWH Modular Data Pipeline

**Group Zeta · DSS150P · release v1.0.0** (a corrected release after instructor review)

Every figure in this report comes from a command run on the real data; the evidence file is named beside it (`docs/evidence/`).

## 1. Problem and Objectives

The DPWH publishes infrastructure contract data for transparency, but the records are not ready for analysis: fields are text, dates follow mixed rules, joint-venture contractors share one field, and region names do not match the official codes. The portal's API is also closed to automated clients.

Objectives:

1. Build a modular, rerun-safe pipeline from published sources to PostgreSQL, with every row traceable to its source file.
2. Check every layer on every run (hashes, row counts, reconciliation, recomputed calculations).
3. Compute a delay flag that can be compared across regions, and the award amount relative to the approved budget (`award_to_abc_pct`, `award_savings_php`).
4. Describe delays by region and per person, and explore which recorded attributes separate delayed from on-schedule contracts.

The project does not measure cost overruns: `project_cost` (the source `budget`) is inconsistent, matching the approved budget in 9.8% of rows and the award amount in 50.0%, so no overrun measure can be defined and validated from it.

## 2. Data Sources

| Source | Role | Licence | Retrieval |
| --- | --- | --- | --- |
| BetterGov.ph DPWH Infrastructure Transparency Dataset, Hugging Face, revision `648ea96` | Primary: 248,421 contracts, 52 columns | CC0 1.0 | Automated download of the pinned revision; SHA-256 equals the published value |
| PSA Philippine Standard Geographic Code, 2Q 2026 | Official reference for the 18 regions | CC BY 4.0 | Manual browser download; SHA-256 recorded at download |
| PSA OpenSTAT, 2024 Census of Population by region | Population for per-capita comparison | CC BY 4.0 | Automated through the documented PX-Web API |
| DPWH transparency API | Original source | None published | Not collected |

The DPWH transparency API is the original source, but the team did not collect from it. On 30 September 2026 four runs of the team's extractor received HTTP 403 from Cloudflare bot protection on their first data request, and the extractor stopped each time, as designed (evidence 08). The team did not try to get around the block and adopted BetterGov's published CC0 release instead. BetterGov states that it collected the data with a third-party scraper using browser impersonation; this is disclosed in `docs/sources.md`, and the team did not use that method. The release covers about 93.5% of the portal's 265,582 projects and reflects the portal around January 2026.

Every automated retrieval reads robots.txt, identifies itself with the team's contact address, keeps to the publisher's rate limit, stops on HTTP 401, 403 or any bot-protection challenge, and saves nothing unless the SHA-256 matches. OpenSTAT serves robots.txt behind a challenge; because the PSA documents its API for programs (at most 10 requests per 10 seconds), the pipeline records that and calls only the documented API, and still stops if the API itself challenges. OpenSTAT is the third permitted source: an official, openly licensed table with a documented integration purpose (per-capita comparison by PSGC region). A NOAA rainfall source was also built and tested, then removed before release: its 16 Philippine stations covered only 8 of the 18 regions, and its terms do not allow results from non-U.S. stations to be passed on (`docs/sources.md`). Source diversity for the contract records themselves remains limited: they come from one third-party release, the DPWH API is blocked, and PhilGEPS is pending because its terms page returned HTTP 404. The contract values therefore cannot be cross-checked against an independent record, which is the remaining gap against the source-diversity criterion.

## 3. Architecture

![System architecture](images/architecture.png)

Two Compose files deploy the system: `docker-compose.yml` runs PostgreSQL 16 (`postgres`) and the pipeline image (`pipeline`), and `docker-compose.airflow.yml` adds Airflow 2.10.5 (`airflow`). Every step is one `python -m src.cli` command, so it runs the same by hand or under Airflow. The full diagram, checked against the DAG and both Compose files, is in `docs/architecture.md`.

## 4. Implementation

Python 3.11, pandas 3, pyarrow, SQLAlchemy 2 with psycopg2, scikit-learn, matplotlib, protego (robots.txt), PyYAML and python-dotenv, pinned in `requirements.txt`.

| Package | Modules | Responsibility |
| --- | --- | --- |
| `src/extract` | `file_source.py`, `api_source.py`, `http_client.py`, `raw_store.py`, `dpwh_projects.py` | Pinned downloads, API sources, the polite client, the raw store with SHA-256 manifests, and the blocked DPWH extractor kept for reference |
| `src/transform` | `staging.py`, `cleaning.py`, `contractors.py`, `curated.py`, `population.py`, `pxweb.py`, `jsonstat.py`, `partition.py` | Typing and cleaning, joint-venture splitting, curated metrics, reference tables, partitioning |
| `src/load` | `postgres.py`, `partitions.py` | Hash-guarded upsert and partition loads with audit rows |
| `src/validate` | `pipeline.py` | The `validate` command |
| `src/benchmark` | `formats.py` | Storage benchmark |
| `src/analytics` | `full_analysis.py`, `data_product.py` | Analysis and the reviewer sample |

The data layers are folders under `data/`: `source`, `raw`, `staging`, `curated`, `quarantine`, `partitioned`, `benchmarks` and `analytics`. Every run writes to its own `run_id` folder, and raw files are never modified. The automated tests pass (`pytest -q`: 144 passed, 1 skipped).

## 5. Data Model

The curated contracts (248,418 rows × 43 columns) are written as Parquet and partitioned by `start_year` and `start_month`: 125 partitions plus a null partition for the 7,669 contracts without a start date (evidence 22). PostgreSQL holds `curated.dpwh_projects`, keyed on `contract_id` with CHECK constraints (cost at least 0, progress 0 to 100, a 64-character hex `record_hash`) and indexes on `status_name` and `start_date`, and `audit.partition_loads`, which records every partition load with its counts, status and UTC timestamps. There are no foreign keys; the audit table refers to runs and partitions by identifier. Reference tables (`psgc_regions`, `region_population`) are curated Parquet files kept apart from the contracts, so they never change a contract's `record_hash`.

## 6. Transformations and Cleaning

Staging types every column, parses both date formats, turns the 1900-01-01 placeholder into null and splits contractors into 253,273 member rows; 11,591 contracts are joint ventures. Curated flags each region against the PSGC (99.9% of contracts match; the rest are Central Office), adds `award_savings_php`, `award_to_abc_pct`, the delay flag and a deterministic `record_hash`, and quarantines 3 contracts with progress outside 0 to 100. A contract is delayed when it is On-Going, past its expiry date and below 100% progress as of 22 January 2026. No row is dropped silently: rejected rows go to `data/quarantine/` with an error code.

## 7. Validation

`python -m src.cli validate` (`src/validate/pipeline.py`) reads every layer back: raw files against `manifest.jsonl` and the expected SHA-256, every staging, curated and quarantine output against its recorded SHA-256 and row count, row reconciliation from raw through staging and curated to the partitions, recomputed `record_hash`, award metrics, delay rule and region flag, the coverage of the population table, and the database hashes against the curated file. It exits with code 1 on any failure, so Airflow marks the task failed. Before v1.0.0 all 30 checks passed (evidence 24); with the OpenSTAT source added, all 35 checks passed with 0 failures (evidence 36).

## 8. Orchestration

The DAG `dss150p_pipeline` has seven BashOperator tasks, extract → stage → curate → partition → load → validate → benchmark, each one CLI command in the separate pipeline virtual environment `/opt/pipeline-venv`, because Airflow 2 pins older SQLAlchemy and pandas than the pipeline needs. It runs daily at 02:00 UTC with catchup off and one active run at a time; tasks retry twice with exponential backoff (1 to 10 minutes) and have timeouts; a failure callback logs the traceback. Parameters select full or partition mode, the year and month, and a controlled failure. Three runs succeeded, including the scheduled one (evidence 30), and the v1.0.0 DAG, whose extract task downloads and verifies every source, ran end to end in the container (evidence 39). With `controlled_failure`, validate failed on attempt 1 by design and passed on attempt 2 with 29 checks, because a partition run skips the extra-rows check (evidence 31). In a tamper test, an edited curated file was caught as a checksum mismatch and validate exited 1; after the rebuild it passed and the reload changed 0 rows (evidence 28).

## 9. Deployment and Reproducibility

The pipeline image is a single-stage `python:3.11-slim` build that runs as a non-root user; Airflow is built from `apache/airflow:2.10.5-python3.11` with the pipeline packages in their own virtual environment. Secrets and contact details live only in `.env`, which git ignores. From a clean clone the whole chain reruns with one manual step, downloading the PSGC workbook (README "Full run"); a second team member rebuilt it on another machine (evidence 38). The loader is idempotent: the first load inserted 248,418 rows and the rerun reported 0 inserted, 0 updated, 248,418 unchanged (evidence 23); one partition, 2023-05, loaded 3,002 rows with an audit row (evidence 25).

A reviewer can inspect the result without a rebuild in `docs/data_product/`: a 1,000-row sample of the curated contracts (`random_state=42`, CSV and Parquet), the region population table, `insights.md`, `analytics_metrics.json`, the charts and `SHA256SUMS`.

## 10. Results and Analytics

Storage benchmark on 248,418 rows × 43 columns, median of 5 runs (evidence 26): Parquet with Zstandard is 33.9 MB against 219.8 MB for CSV, about 6.5 times smaller, and filters the On-Going contracts in 0.092 s against 3.26 s, about 35 times faster. JSON Lines is the largest (415.0 MB) and slowest; PostgreSQL is 226.1 MB and filters in 0.54 s because it returns only the 34,728 matching rows.

Descriptive findings (evidence 27, 37): 34,728 of the 248,418 curated contracts are On-Going and 23,769 of them (68.4%) are delayed as of 22 January 2026. Among regions with at least 30 On-Going contracts, Region I is highest at 80.2% of 3,039 and Region X lowest at 50.5% of 1,751. Delayed and on-schedule contracts have about the same median cost (ratio 0.99). Per 100,000 people, using the 2024 census population of each PSGC region, the Cordillera Administrative Region has the most delayed On-Going contracts (60.09; 1,087 delayed, population 1,808,985) and Region XII (SOCCSKSARGEN) the fewest among regions with contracts (8.47); NCR is at 11.14. BARMM has no contracts in the source and is left out of the comparison. Per-capita counts ignore project size, cost and need.

Exploratory classification: scikit-learn's HistGradientBoostingClassifier on the 34,728 On-Going contracts, tuned with a 4-setting grid search and 3-fold cross-validation on an 80/20 split (`random_state=42`). Best settings: learning_rate 0.1, max_depth 6, max_iter 200. Held-out ROC AUC 0.701 and balanced accuracy 0.600. The most informative features by permutation importance are `log_abc_php` (0.095), `infra_year` (0.080) and `region` (0.074). Progress, status and every contract date are excluded because they define the delay flag. This classifies delayed status within one snapshot; it does not forecast future delays or establish causes, which would need dated snapshots and time-based validation. `infra_year` largely measures contract age, so its importance says more about age than about why contracts are delayed.

## 11. Challenges Encountered

1. **Access refused.** The DPWH API returned HTTP 403 from bot protection; the team stopped and used an open, licensed release instead of evading the block.
2. **Dependency conflict.** Airflow 2 pins SQLAlchemy 1.4 and pandas 2.1, while the pipeline needs SQLAlchemy 2.1 and pandas 3; the pipeline runs in its own virtual environment inside the Airflow image.
3. **Airflow metadata.** A custom metadata schema broke DAG-run creation; Airflow's tables were moved back to the default schema.
4. **Instructor review.** The first report did not match the implementation. v1.0.0 corrects it, automates the BetterGov download, adds the OpenSTAT API source, an architecture diagram and a reviewable data product, and marks the old Airflow diagnostics document as historical.
5. **A broken API format.** OpenSTAT's JSON-stat 2.0 output returned a single value of 1 for a 137-row table. The response check (one value per selected row) stopped the run before anything was stored; probes showed the PX-Web JSON format returned the published figures, so the pipeline uses that format and matches regions on their PSGC codes.
6. **A source that did not earn its place.** A NOAA rainfall source was built and tested, then removed: 16 stations in 8 of 18 regions were too coarse for a finding, and its results could not be published.

## 12. Limitations

- One snapshot, about eight months older than the portal and covering about 93.5% of it; no row-level update times; contractor names cut at 50 characters.
- `project_cost` is inconsistent, so cost overruns are not measured.
- The PSGC workbook needs a manual download; PhilGEPS is pending.
- Per-capita values use the 2024 census, not the snapshot date, and ignore project size and need.
- If a raw extract fails halfway, the retry does not clean up the partial run folder.

## 13. Future Improvements

- Collect dated snapshots so delays can be modelled over time with time-based validation.
- Admit an independent record of the same contracts (for example PhilGEPS, once its terms are confirmed) to cross-check values.
- Add notifications to the Airflow failure callback and a dashboard over `curated.dpwh_projects`.

## 14. Evidence

Every result above comes from a captured transcript in [`docs/evidence/`](evidence/). The full index is in the README's Evidence section.

| Evidence | What it shows |
| --- | --- |
| [08](evidence/08_api_runs_blocked.txt) | The four blocked DPWH API runs |
| [22](evidence/22_clean_room_full_run.txt) | Clean-room rebuild of every layer and the partitions |
| [23](evidence/23_second_load_rerun.txt) | Load rerun: 0 inserted, 0 updated |
| [24](evidence/24_validate.txt) | Validation before v1.0.0: 30 checks passed |
| [25](evidence/25_load_partition.txt) | Partition load with its audit row |
| [26](evidence/26_benchmark.txt) | Storage benchmark |
| [27](evidence/27_analyze.txt) | Analysis before v1.0.0: snapshot date, exclusions and model results |
| [28](evidence/28_failure_and_recovery.txt) | Tamper test and recovery |
| [30](evidence/30_airflow_dag_runs.txt), [31](evidence/31_airflow_task_logs.txt), `docs/images/airflow_*.png` | Current Airflow DAG runs, controlled failure and retry |
| 32–38 | v1.0.0: automated retrieval of every source, unchanged rerun, rebuild, load, validation, analysis and the rebuild on a second machine |
| 39 | v1.0.0 DAG run in the Airflow container |

`docs/evidence/airflow_diagnostics.md` describes an older CSV pipeline and is kept only as history; it is not evidence for the current DAG.
