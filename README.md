# DSS150P Zeta: DPWH Modular Data Pipeline

Group Zeta's modular, rerun-safe data pipeline for DPWH infrastructure project data, built to the course's Modular Data Pipeline Specification (Python, PostgreSQL, Parquet, Docker Compose, Apache Airflow).

**Status:** Milestone 1 complete (environment validated on WSL and in Docker). Milestone 2 in progress: raw ingestion of the primary source is complete; staging is next.

## Data sources

The pipeline's primary source is the **BetterGov.ph DPWH Infrastructure Transparency Dataset**, a CC0-licensed release of DPWH transparency portal data published on Hugging Face (revision `648ea96`, January 2026, 248,421 contracts). Full provenance, the publisher's stated collection method, and the admission rules for any further source are in `docs/sources.md`.

How the project arrived there:

1. The DPWH transparency portal (`transparency.dpwh.gov.ph`) loads its data from a public JSON API at `api.transparency.dpwh.gov.ph`. The team built a polite extractor for it, described below.
2. In four runs on 30 September 2026 the extractor was blocked by Cloudflare bot protection with HTTP 403 on its first data request, once on `/ai/stats` and three times on `/projects`. It stopped each time, as designed, and automated collection from the API was ended. The team did not attempt to get around the block.
3. The portal's summary figures were captured once by hand in a browser and recorded in `docs/reconciliation_baseline.md` (265,582 projects) for reconciliation.
4. The team adopted BetterGov's published release instead. Its README states that BetterGov collected the data with a third-party scraper using browser fingerprint impersonation; the team did not collect it and does not use that method. The release covers about 93.5% of the portal's current total and is roughly eight months older than the baseline.

Data credit: BetterGov.ph, compiled from the DPWH Transparency Portal.

## Raw ingestion of the primary source (Milestone 2)

Download `dpwh_transparency_data_all_details.parquet` from the dataset page (Files and versions tab) into `data/source/`, then run:

```bash
python -m src.cli extract-file
```

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
| `data/raw/source=dpwh_api/run_id=.../` | API extractor runs (blocked) |

## Repository layout

| Path | Purpose |
| --- | --- |
| `config/settings.yml` | Non-secret pipeline defaults |
| `.env.example` | Template for environment-specific values (copy to `.env`, never commit) |
| `src/cli.py` | Unified command line entry point |
| `src/config.py` | Settings and environment loading |
| `src/extract`, `transform`, `load`, `validate`, `benchmark` | Pipeline modules (filled in by later milestones) |
| `docs/` | Source register, reconciliation baseline, data contract |
| `dags/` | Airflow DAG (Milestone 4) |
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
docker compose down
```

If port 5432 is already used on your machine, set `POSTGRES_PORT` to another value such as `5433` in `.env`. The pipeline container always reaches the database on the internal port 5432.

## Commit and tag conventions

- Commit messages: `Lastname - Action - module - short description`, for example `Risma - Fix - extract - recreated code structure`.
- Work happens on short-lived branches; `main` stays clean.
- Milestone breakthroughs are marked with semantic tags (`v0.1.0`, `v0.2.0`, ...).
