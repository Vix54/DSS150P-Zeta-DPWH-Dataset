# Handoff: pipeline implemented end to end; run evidence and Airflow screenshots outstanding

This note records where the pipeline stands and what the next owners need to continue. Read `docs/sources.md` and `docs/data_contract.md` alongside it.

## State of the repository

| Item | State |
| --- | --- |
| `main`, tag `v0.1.0` | Milestone 1: environment, configuration, Docker Compose, `validate-env` |
| `main`, tag `v0.2.0` | Raw ingestion: API extractor (blocked, kept for reference) and checksum-verified file ingestion of the primary source |
| `main`, tags `v0.2.1`, `v0.2.2` | Spec-aligned `extract_sources(run_id)`, evidence transcripts, and the official PSGC reference source |
| `main`, tag `v0.3.0` | Staging and curated layers on the BetterGov source, with quarantine, PSGC region check, metrics and `record_hash` |
| Branch `feat/m3-load-validate` | Database load, partitioning and partition loads, `validate`, benchmarks, corrected analytics, Airflow DAG and container (next tag `v0.4.0`) |
| Tests | 105 passing, 1 database test skipped unless a scratch database is configured (`pytest -q`) |

## Reproduce the raw layer from a fresh clone

```bash
git clone https://github.com/Vix54/DSS150P-Zeta-DPWH-Dataset.git
cd DSS150P-Zeta-DPWH-Dataset
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in every `change_me` value in `.env` (use your own contact for `SCRAPER_CONTACT`; never commit `.env`). Then download `dpwh_transparency_data_all_details.parquet` from https://huggingface.co/datasets/bettergovph/dpwh-transparency-data (Files and versions tab) into `data/source/` and run:

```bash
python -m src.cli validate-env
python -m src.cli extract-file
```

`extract-file` refuses the file unless its SHA-256 equals the published value `953f0bf9…a7c97`, then stores it unchanged in `data/raw/source=bettergov_hf/run_id=<UTC timestamp>/` with `manifest.jsonl` and `run.json`. Running it again with the same file writes nothing.

## What the raw layer contains

Two admitted sources. The official reference is the PSA's Philippine Standard Geographic Code (PSGC) as of 30 June 2026, an Excel workbook under CC BY 4.0, ingested with `python -m src.cli extract-file --source psa_psgc` into `data/raw/source=psa_psgc/` for validating regions and provinces (see `docs/sources.md`; reading it with pandas needs `openpyxl`, which is not yet in `requirements.txt`). The primary source is the BetterGov.ph CC0 release of DPWH transparency portal data (revision `648ea96`, January 2026). Its `_all_details` file already combines BetterGov's project listing with per-contract procurement details, so it is the merged raw input for the pipeline. The smaller `dpwh_transparency_data.parquet` from the same release is a strict subset (248,220 of its IDs, all present in `_all_details`) and is not ingested.

| Fact | Value |
| --- | --- |
| Rows | 248,421, one per unique `contractId` |
| Columns | 52 |
| Coverage | 93.5% of the 265,582 projects the portal reported on 30 September 2026 |
| Collection method | Stated by the publisher: a third-party scraper using browser fingerprint impersonation. Not collected by the team; disclosed in `docs/sources.md` and to be disclosed in the report |

Load the raw file for EDA without touching it:

```python
import pandas as pd
from pathlib import Path

raw_dir = sorted(Path("data/raw/source=bettergov_hf").glob("run_id=*"))[-1]
df_raw = pd.read_parquet(raw_dir / "dpwh_transparency_data_all_details.parquet")
df = df_raw.copy()
```

## Facts already established about the raw data

| Area | Finding |
| --- | --- |
| Duplicate columns | `infraType_1`, `latitude_1`, `longitude_1` equal `infraType`, `latitude`, `longitude` in 100% of rows |
| `budget` | Equals `abc` in 9.8% of rows and `awardAmount` in 50.0%; about 40% match neither. Its meaning is inconsistent, so do not derive values from it |
| `amountPaid` | Zero in every row |
| `abc`, `awardAmount`, `infraYear` | Stored as text |
| `status_1` | Undocumented codes: F 138,363; A 103,747; CP 3,747; null 1,830; T 498; ANV 186; FNV 45; TNV 3; C 2 |
| Dates | Mostly ISO (bid dates carry seven decimal places of seconds); 9 rows from office `25FI` use `MM/DD/YYYY hh:mm:ss AM/PM`; 1 January 1900 is used as "no date" in 446 advertisement dates and 186 bid deadlines |
| Contractor | `NAME (id)`, `NAME ([REVOKED] id)`, joint ventures `A (id) / B (id)` (11,625 strings), former names in `(FORMERLY ...)`, `(PREVIOUSLY ...)`, `(FOR. ...)` |
| Contractor name limits | Names are cut at 50 characters (12,209 of 12,818 unbalanced names); the longest name is 100 characters |
| `winnerNames` | Contractor members without IDs, sorted ignoring punctuation and spaces, joined with ", "; empty when there is no contractor |
| No contractor | 6,808 rows: all 6,800 For Procurement, plus 7 Completed and 1 Not Yet Started |
| Progress | Three negative values (-100, -0.1, -0.02) |
| Nested fields | `components`, `bidders`, `coordinates` are lists of records; their inner fields are not yet profiled |
| Document links | Point to `dcs.infrawatch.ph`; store as text, do not fetch |

Status in the release compared with the portal baseline (`docs/reconciliation_baseline.md`):

| Status | Release (Jan 2026) | Portal (30 Sep 2026) | Difference |
| --- | --- | --- | --- |
| Completed | 205,109 | 214,535 | +9,426 |
| On-Going | 34,730 | 30,058 | −4,672 |
| For Procurement | 6,800 | 19,065 | +12,265 |
| Terminated | 929 | 1,037 | +108 |
| Not Yet Started | 853 | 887 | +34 |
| Total | 248,421 | 265,582 | +17,161 |

The pattern fits a snapshot about eight months older than the baseline: projects moved from ongoing to completed, and most of the gap is new projects in procurement.

## Staging and curated (on `main`, tag `v0.3.0`)

Both layers run through the command line and follow `docs/data_contract.md`. On the real data (raw run `20261005T165803Z`):

| Step | Command | Result |
| --- | --- | --- |
| Staging | `python -m src.cli stage` | 248,421 rows staged, 0 quarantined; 253,273 contractor member rows (11,591 joint ventures after removing repeated members) |
| Curated | `python -m src.cli curate` | 248,418 curated, 3 quarantined (progress outside 0 to 100); 248,121 contracts matched to a PSGC region, with only "Central Office" unmatched; 23,769 contracts delayed as of 22 January 2026 |

Decisions already taken:

- Staging quarantines only structurally unusable rows; everything else is kept with warning codes so it still counts in reconciliation.
- The second date format is parsed and 1 January 1900 is treated as null, with traceable warnings.
- Repeated contractor members (34 contracts) are de-duplicated and joint ventures are counted on unique members.
- Curated keeps the column names the database load expects (`contract_id`, `project_cost`, `physical_accomplishment`, `start_date`, `infra_year`, `is_delayed`, `status_name`) and adds `record_hash` and `processed_at_utc`.
- The PSGC only flags regions; it never drops or overwrites rows.
- `is_delayed` uses the contract expiry date, because `completion_date` is empty for every On-Going contract.

For loading and partitioning, `curated_contracts_path(settings)` in `src/transform/curated.py` returns the latest `dpwh_contracts.parquet`. The old `stage_dpwh_data` and `curate_dpwh_data` functions were replaced, so the DAG should call the CLI commands.

Open question: 40 contracts still have `W_WINNER_NAMES_MISMATCH` after de-duplication.

## Load, partitioning, validation, benchmarks and Airflow (implemented, evidence pending)

| Area | State |
| --- | --- |
| PostgreSQL | `init-db`, then `load`: hash-guarded upsert into `curated.dpwh_projects`; rows the table rejects go to `data/quarantine/load/` with `Q_LOAD_*` codes; a second load on unchanged data reports `inserted=0, updated=0`. Credentials come from `.env` only |
| Partitioning | `partition` writes `data/partitioned/dpwh_contracts/start_year=YYYY/start_month=M/`; `load-partition --year --month` loads a slice and logs every attempt to `audit.partition_loads` |
| Validation | `validate` checks raw manifests and expected hashes, every layer's recorded checksums and row counts, reconciliation raw -> staging -> curated -> partitions, the curated calculations and the database hashes; exit status 1 on any failure |
| Benchmarks | `benchmark`: CSV, JSON Lines, Parquet (Snappy, Zstandard) and PostgreSQL; write, size, full read and filtered query, median of 5 runs, results in `data/benchmarks/` |
| Analytics (supplementary) | `analyze`: descriptive statistics, charts and a delay model with insights generated from the computed numbers, in `data/analytics/` |
| Airflow | `dags/dss150p_pipeline.py` calls only the CLI; `Dockerfile.airflow` and `docker-compose.airflow.yml` run Airflow 2.10.5 with the pipeline's packages in a separate virtual environment. Parsed and run end to end with Airflow 2.10.5 outside Docker during development; the run in Docker and its screenshots and logs are still to be captured |

Still open:

| Area | Next steps |
| --- | --- |
| Evidence (section 7.3) | Transcripts for the full run, the 0/0 second load, `validate`, a partition load, the benchmark and a full clean-room rebuild; Airflow Grid/Graph screenshots, task logs and a `controlled_failure` run |
| Validation extras | Reconcile curated status counts against `docs/reconciliation_baseline.md`; hand-check about 30 contracts drawn with `random_state=42` on the portal in a normal browser; profile the nested `components`, `bidders` and `coordinates` fields; investigate the 40 winner-name mismatches |
| Further sources | Only official sources, used to add fields and flag conflicts, never to overwrite the primary source. PhilGEPS Open Data is pending: its terms link returned 404 and download availability is unconfirmed (`docs/sources.md`) |

## Evidence captured

`docs/evidence/` holds terminal transcripts for the acceptance protocol (section 7.3): environment validation locally and in Docker, the test suite, a clean-room rebuild of the raw layer (first run stores, second run reports unchanged), raw profiling with primary-key uniqueness, the September 2026 API runs that stopped on HTTP 403 (01–08), staging (09–11), PSGC ingestion (12–14), and curated runs including the rebuild after the delay-rule fix (15–19). `airflow_diagnostics.md` documents the team's Airflow runs.

## Rules to keep

- Never modify `data/raw`; never commit anything under `data/` or `.env`.
- No collection that gets around access controls or bot protection; any new source must pass the admission check in `docs/sources.md`.
- Commit format `Lastname - Type of Action - project part - short description`; small commits on short-lived branches; `main` stays clean; semantic tags at milestones (next: `v0.4.0` for load, partitioning, validation and benchmarks).

## Environment notes

- Local development has run on Python 3.14 (WSL) and the Docker image uses Python 3.11; tests pass on both lines.
- Docker: `docker compose up -d postgres`, then `docker compose run --rm pipeline validate-env --check-db`. The schema in `sql/init/` changed in this round, so an existing volume must be recreated once with `docker compose down -v`.
- Airflow: `docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d --build`; set `AIRFLOW_HOST_PORT` in `.env` if 8080 is taken (Lab 3 uses it) and `AIRFLOW_UID` to `id -u`.
