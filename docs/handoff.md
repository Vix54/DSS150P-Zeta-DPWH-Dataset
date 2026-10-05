# Handoff: acquisition complete, over to cleaning, validation, EDA and Airflow

This note records where the pipeline stands at the end of the acquisition work and what the next owners need to continue. Read `docs/sources.md` (on `main`) and `docs/data_contract.md` (on branch `feat/m2-staging`) alongside it.

## State of the repository

| Item | State |
| --- | --- |
| `main`, tag `v0.1.0` | Milestone 1: environment, configuration, Docker Compose, `validate-env` |
| `main`, tag `v0.2.0` | Raw ingestion: API extractor (blocked, kept for reference) and checksum-verified file ingestion of the primary source |
| Branch `feat/m2-staging` | Staging step, data contract and tests, run successfully on the real data. **Not merged.** The cleaning owners decide whether to adopt, change or replace it |
| Tests | 66 passing (`pytest -q`) |

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

## Staging head start (`feat/m2-staging`)

`python -m src.cli stage` implements `docs/data_contract.md`. On the real data (raw run `20261005T154820Z`) it stages all 248,421 rows in about 21 seconds, quarantines none, and writes 253,273 contractor member rows (11,591 joint ventures after removing repeated members). Decisions already taken:

- Quarantine only structurally unusable rows (missing or duplicate `contractId`, unparseable numbers or dates); everything else is kept with warning codes so it still counts in reconciliation.
- Parse the second date format and treat 1 January 1900 as null, with warnings that keep both traceable.
- De-duplicate repeated contractor members (34 contracts) and count joint ventures on unique members.

Open question for the cleaning owners: 40 contracts still have `W_WINNER_NAMES_MISMATCH` after de-duplication.

## Remaining work

| Area | Next steps |
| --- | --- |
| Cleaning (staging) | Review or adopt `feat/m2-staging`; profile the nested `components`, `bidders` and `coordinates` fields before splitting them into tables |
| Curated layer | `contractId` key; deterministic `record_hash` that excludes the volatile fields `is_live`, `livestream_url`, `livestream_video_id`, `livestream_detected_at` (and `reportCount` if a source carries it); metrics such as award versus approved budget and bidder counts |
| PostgreSQL | Curated schema with hash-guarded UPSERT and `audit.partition_loads`; the database runs on host port 5433 because Lab 3 uses 5432 |
| Validation | Check `region` and implementing-office provinces against the PSGC reference; reconcile curated status counts against `docs/reconciliation_baseline.md` and explain the gap; hand-check about 30 contracts drawn with `random_state=42` on the portal in a normal browser; consistency checks (dates, amounts, coordinates) |
| Milestone 3 | Benchmark CSV, Parquet (Snappy, Zstd) and PostgreSQL on size and read/write time into `data/benchmarks/`; Hive-partitioned copy in `data/partitioned/` (candidate keys `infra_year` and `region`) |
| Milestone 4 | DAG at `dags/dss150p_pipeline.py` that only calls the CLI commands (`extract-file`, `stage`, later steps); Airflow needs a host port other than 8080 because Lab 3 uses it. Set `PIPELINE_RUN_ID` for every task to one path-safe value per DAG run, for example `airflow__{{ ts_nodash }}` (letters, digits, `.`, `_` and `-` only): `extract-file` uses it as the raw `run_id` through `extract_sources(run_id)`, and `stage` writes it to the `pipeline_run_id` column |
| Further sources | Only official sources, used to add fields and flag conflicts, never to overwrite the primary source. PhilGEPS Open Data is pending: its terms link returned 404 and download availability is unconfirmed (`docs/sources.md`) |

## Evidence captured

`docs/evidence/` holds terminal transcripts for the acceptance protocol (section 7.3): environment validation locally and in Docker, the test suite, a clean-room rebuild of the raw layer (first run stores, second run reports unchanged), raw profiling with primary-key uniqueness, and the September 2026 API runs that stopped on HTTP 403. Staging transcripts are on `feat/m2-staging` in the same folder.

## Rules to keep

- Never modify `data/raw`; never commit anything under `data/` or `.env`.
- No collection that gets around access controls or bot protection; any new source must pass the admission check in `docs/sources.md`.
- Commit format `Lastname - Type of Action - project part - short description`; small commits on short-lived branches; `main` stays clean; semantic tags at milestones (next: `v0.3.0` for staging and curated end to end).

## Environment notes

- Local development has run on Python 3.14 (WSL) and the Docker image uses Python 3.11; tests pass on both lines.
- Docker: `docker compose up -d postgres`, then `docker compose run --rm pipeline validate-env --check-db`.
