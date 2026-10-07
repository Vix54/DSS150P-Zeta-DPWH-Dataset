# DSS150P Zeta: DPWH Modular Data Pipeline

Group Zeta's modular, rerun-safe data pipeline for DPWH infrastructure project data, built to the course's Modular Data Pipeline Specification (Python, PostgreSQL, Parquet, Docker Compose, Apache Airflow).

**Status:** Milestones 1 to 3 implemented: raw ingestion of the primary source and the PSGC reference, staging, curated, hash-guarded PostgreSQL load, partitioning and partition loads, the `validate` contract checker and storage benchmarks. Milestone 4: the Airflow DAG runs in its container, including a controlled failure that recovers on retry (evidence 30 and 31, screenshots in `docs/images/airflow_*.png`).

## Data sources

The pipeline's primary source is the **BetterGov.ph DPWH Infrastructure Transparency Dataset**, a CC0-licensed release of DPWH transparency portal data published on Hugging Face (revision `648ea96`, January 2026, 248,421 contracts). Full provenance, the publisher's stated collection method, and the admission rules for any further source are in `docs/sources.md`.

How the project arrived there:

1. The DPWH transparency portal (`transparency.dpwh.gov.ph`) loads its data from a public JSON API at `api.transparency.dpwh.gov.ph`. The team built a polite extractor for it, described below.
2. In four runs on 30 September 2026 the extractor was blocked by Cloudflare bot protection with HTTP 403 on its first data request, once on `/ai/stats` and three times on `/projects`. It stopped each time, as designed, and automated collection from the API was ended. The team did not attempt to get around the block.
3. The portal's summary figures were captured once by hand in a browser and recorded in `docs/reconciliation_baseline.md` (265,582 projects) for reconciliation.
4. The team adopted BetterGov's published release instead. Its README states that BetterGov collected the data with a third-party scraper using browser fingerprint impersonation; the team did not collect it and does not use that method. The release covers about 93.5% of the portal's current total and is roughly eight months older than the baseline.

Data credit: BetterGov.ph, compiled from the DPWH Transparency Portal.

## Raw ingestion of the primary source (Milestone 2)

Download `dpwh_transparency_data_all_details.parquet` from the dataset page (Files and versions tab) and `PSGC-2Q-2026-Publication-Datafile.xlsx` from the PSA into `data/source/`, then run:

```bash
python -m src.cli extract-file --source all
```

`--source bettergov_hf` (the default) or `--source psa_psgc` ingests one source only.

The command checks the file's SHA-256 against the published value recorded in `config/settings.yml`, then copies it byte for byte into `data/raw/source=bettergov_hf/run_id=<UTC timestamp>/` with a `manifest.jsonl` entry and a `run.json` holding the publisher, dataset URL, revision, licence, collection method, and ingestion time. A mismatched checksum stops the run without writing anything. Running the command again with the same file writes nothing and reports the existing run.

## DPWH API extractor (blocked, kept for reference)

Checks made before collection (30 September 2026):

| Check | Finding |
| --- | --- |
| `api.transparency.dpwh.gov.ph/robots.txt` | HTTP 404, no crawler rules published for the API host |
| `transparency.dpwh.gov.ph/robots.txt` | Written for search engines; `/api/` is listed under private/admin areas of the portal host and `?page=` under duplicate-content rules; crawl delay 0.1 s |
| Terms of use or data policy | None published on the portal |
| Bot protection | Cloudflare is present. All four live runs (30 September 2026) were blocked with HTTP 403 on their first data request and stopped immediately (run records in `docs/evidence/08_api_runs_blocked.txt`) |

How the extractor behaves:

- reads and obeys `robots.txt` for every host it contacts, with wildcard support;
- identifies itself with a descriptive user-agent and a contact address taken from `.env`;
- sends one request at a time, at least 1.5 seconds apart (15 times slower than the portal's stated crawl delay);
- retries a limited number of times on HTTP 429 and 5xx, honouring `Retry-After`;
- stops immediately, without retrying, on HTTP 401 or 403 or any bot-protection challenge, and never attempts to bypass one;
- stores every response byte-for-byte in the raw layer with a SHA-256 manifest, and never overwrites a stored file.

```bash
python -m src.cli extract --max-pages 3
python -m src.cli extract --resume <run_id>
```

Runs are written to `data/raw/source=dpwh_api/run_id=<UTC timestamp>/` with the listing pages, `manifest.jsonl`, and `run.json` (status, stop reason, request counts, robots.txt results).

## Raw layer layout

Each source has its own lane, so sources never mix before staging:

| Path | Content |
| --- | --- |
| `data/raw/source=bettergov_hf/run_id=.../` | Primary source file, unchanged |
| `data/raw/source=psa_psgc/run_id=.../` | Official PSGC reference workbook, unchanged |
| `data/raw/source=dpwh_api/run_id=.../` | API extractor runs (blocked) |

## Staging (Milestone 2)

```bash
python -m src.cli stage
python -m src.cli stage --rebuild
```

Staging reads the latest raw run of the primary source (checksum verified first), applies the rules in `docs/data_contract.md`, and writes:

| Path | Content |
| --- | --- |
| `data/staging/source=bettergov_hf/run_id=<run>/contracts.parquet` | One typed, normalised row per contract, with `warning_codes` and lineage columns |
| `data/staging/source=bettergov_hf/run_id=<run>/contract_contractors.parquet` | One row per contractor on a contract (joint ventures split into members) |
| `data/staging/source=bettergov_hf/run_id=<run>/staging_report.json` | Row accounting, quarantine and warning counts, status counts, output checksums |
| `data/quarantine/source=bettergov_hf/run_id=<run>/contracts.parquet` | Rows that cannot be used, exactly as read from raw, with `error_codes` |

Only structurally broken rows (no or duplicated contract ID, unparseable numbers or dates) are quarantined. Unusual but usable rows stay in staging with warning codes so they still count in reconciliation. Every input row is either staged or quarantined. Staging the same raw run again does nothing unless `--rebuild` is given.

## Curated (Milestone 2)

```bash
python -m src.cli curate
python -m src.cli curate --rebuild
```

Curated reads the latest staging run and the PSGC reference (both checksum verified) and writes `data/curated/run_id=<staging run>/dpwh_contracts.parquet`, the contractor members, a region match table and `curated_report.json`. It flags regions against the PSGC, adds award metrics, `is_delayed`, `processed_at_utc` and a deterministic `record_hash`, and quarantines progress values outside 0 to 100 in `data/quarantine/curated/`. Reading the PSGC workbook needs `openpyxl` (pinned in `requirements.txt`). Rules are in `docs/data_contract.md`.

## Database load (Milestone 2)

```bash
python -m src.cli init-db
python -m src.cli load
python -m src.cli load
```

`init-db` applies `sql/init/*.sql` (schemas `curated` and `audit`, table `curated.dpwh_projects`, table `audit.partition_loads`). Docker applies the same files automatically when the Postgres volume is first created, so `init-db` is only needed for a database that already exists.

`load` reads the latest curated run (checksum verified), checks each row against the table's constraints, sends rows the table cannot accept to `data/quarantine/load/run_id=<run>/scope=full/` with an error code (for example `Q_LOAD_NEGATIVE_PROJECT_COST`), and upserts the rest with `INSERT ... ON CONFLICT (contract_id) DO UPDATE ... WHERE record_hash IS DISTINCT FROM EXCLUDED.record_hash`. The second run on unchanged data must report `inserted=0, updated=0`.

## Partitioning and partition loads (Milestone 3)

```bash
python -m src.cli partition
python -m src.cli load-partition --year 2023 --month 5
python -m src.cli load-partition --year 2023
```

`partition` writes the curated contracts as Hive-style Parquet under `data/partitioned/dpwh_contracts/start_year=YYYY/start_month=M/part-0.parquet`, with `start_year` and `start_month` taken from `start_date`. Contracts without a start date go to the `__HIVE_DEFAULT_PARTITION__` folder, so no row is dropped. `_manifest.json` records the curated run, row counts and a SHA-256 per file.

`load-partition` reads one slice (checksum verified), loads it with the same hash-guarded upsert and writes one row to `audit.partition_loads` with the pipeline run ID, slice, counts, status and UTC timestamps. A failed attempt is also recorded, with its error message.

## Validation

```bash
python -m src.cli validate
python -m src.cli validate --skip-db
python -m src.cli validate --year 2023 --month 5
```

`validate` exits with status 1 if any check fails. It checks raw files against `manifest.jsonl` and the expected SHA-256 values, every staging, curated and quarantine output against its recorded SHA-256 and row count, row reconciliation raw -> staging -> curated -> partitions, the calculation invariants (`record_hash` recomputed, award metrics, delay rule, PSGC flag, progress range, unique `contract_id`), and that every loadable curated contract is in the database with the same `record_hash`. `--year`/`--month` limit the database check to one slice after a partition load.

## Benchmarks (Milestone 3)

```bash
python -m src.cli benchmark
```

Writes the curated contracts as CSV, JSON Lines, Parquet (Snappy), Parquet (Zstandard) and a PostgreSQL table, and measures write time, size on disk (`pg_total_relation_size` for PostgreSQL), full read time and a filtered query (`status_name = 'On-Going'`), each over 5 runs with the median reported. Results go to `data/benchmarks/benchmark_results.csv` and `benchmark_runs.json` (every run, plus the machine and library versions). On the full dataset this takes several minutes.

## Analytics (supplementary)

```bash
python -m src.cli analyze
```

Descriptive statistics, four charts and a delay model on the curated contracts, written to `data/analytics/` (`insights.md`, `analytics_metrics.json`, PNG charts). Every sentence in `insights.md` is generated from the computed numbers. The model only uses On-Going contracts and excludes the columns that define the delay flag (progress, status and the contract dates).

## Full run

```bash
python -m src.cli validate-env --check-db
python -m src.cli extract-file --source all
python -m src.cli stage
python -m src.cli curate
python -m src.cli partition
python -m src.cli load
python -m src.cli validate
python -m src.cli load
python -m src.cli benchmark
```

Every step is rerun-safe: on unchanged input, `extract-file`, `stage`, `curate` and `partition` report "Unchanged" and the second `load` reports `inserted=0, updated=0`. For a clean-room rebuild, empty `data/raw`, `data/staging`, `data/curated`, `data/quarantine` and `data/partitioned` (keep the `.gitkeep` files) and run the sequence again.

## Airflow (Milestone 4)

The DAG `dags/dss150p_pipeline.py` runs `extract >> stage >> curate >> partition >> load >> validate >> benchmark`. Every task is a `BashOperator` that calls the CLI above; no pipeline logic lives in the DAG. It has 2 retries with backoff, a timeout per task, a failure callback that logs the run, task, try and traceback, a daily schedule (`0 2 * * *`), `catchup=False`, and sets `PIPELINE_RUN_ID=airflow__<logical timestamp>` for every task.

Parameters when triggering: `run_mode` (`full` or `partition`), `target_year`, `target_month`, and `controlled_failure`, which makes `validate` fail on its first try so the retry and recovery can be shown.

```bash
docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.airflow.yml logs -f airflow
docker compose -f docker-compose.yml -f docker-compose.airflow.yml down
```

The UI is at `http://localhost:<AIRFLOW_HOST_PORT>` (default 8080; set another port in `.env` if 8080 is taken), with the user and password from `_AIRFLOW_WWW_USER_USERNAME` and `_AIRFLOW_WWW_USER_PASSWORD`. On Linux and WSL set `AIRFLOW_UID` in `.env` to the output of `id -u` so files written to `data/` stay yours. `Dockerfile.airflow` builds Airflow 2.10.5 with the pipeline's own packages in a separate virtual environment (`/opt/pipeline-venv`), because the pipeline's pins (SQLAlchemy 2, pandas 3) do not fit Airflow 2's own requirements. Airflow keeps its own metadata tables in the `public` schema of the same database; the pipeline's tables live in `curated` and `audit`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `config/settings.yml` | Non-secret pipeline defaults |
| `.env.example` | Template for environment-specific values (copy to `.env`, never commit) |
| `src/cli.py` | Unified command line entry point |
| `src/config.py` | Settings and environment loading |
| `src/extract` | Raw acquisition (file sources, blocked API extractor) |
| `src/transform` | Staging, curated, partitioning |
| `src/load` | PostgreSQL connection, hash-guarded upsert, partition loads and audit |
| `src/validate` | Contract and integrity checks (`validate`) |
| `src/benchmark` | Storage format benchmarks |
| `src/analytics` | Supplementary descriptive analysis and delay model |
| `docs/` | Source register, source contract, data contract, data dictionary, lineage, ERD, architecture review (Section 8 answers), evidence |
| `dags/` | Airflow DAG |
| `Dockerfile.airflow`, `docker-compose.airflow.yml` | Airflow container |
| `sql/init/` | Database initialisation scripts |
| `data/` | Generated data layers (contents are git-ignored) |
| `tests/` | Automated tests |

## Quick start (WSL)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and replace every `change_me` value, then run:

```bash
python -m src.cli validate-env
pytest
```

## Quick start (Docker)

```bash
docker compose up -d postgres
docker compose run --rm pipeline validate-env --check-db
docker compose run --rm pipeline load
docker compose down
```

`docker compose down` keeps the database volume; `docker compose down -v` deletes it, and the next `up` recreates the schema from `sql/init/`. If port 5432 is already used on your machine, set `POSTGRES_PORT` to another value such as `5433` in `.env`. The pipeline container always reaches the database on the internal port 5432.

The database tests in `tests/test_downstream.py` drop and recreate the `curated` and `audit` schemas, so they only run against a scratch database: set `POSTGRES_DB` to a name ending in `_test` and `ZETA_TEST_DB` to the same name. Otherwise they are skipped.

## Commit and tag conventions

- Commit messages: `Lastname - Action - module - short description`, for example `Risma - Fix - extract - recreated code structure`.
- Work happens on short-lived branches; `main` stays clean.
- Milestone breakthroughs are marked with semantic tags (`v0.1.0`, `v0.2.0`, ...).
