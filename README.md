# DSS150P Zeta: DPWH Modular Data Pipeline

Group Zeta's modular, rerun-safe data pipeline for DPWH infrastructure project data, built to the course's Modular Data Pipeline Specification (Python, PostgreSQL, Parquet, Docker Compose, Apache Airflow).

**Status:** Milestone 1 complete (environment validated on WSL and in Docker). Milestone 2 in progress (extraction).

## Data source and scraping ethics

The data comes from the public DPWH transparency portal (`transparency.dpwh.gov.ph`). The portal is a client-rendered web app: its contracts table is filled by the browser from a public JSON API at `api.transparency.dpwh.gov.ph`. The extractor requests the same listing endpoint the portal's own front end uses (`/projects?page=N&limit=50`) plus the `/ai/stats` summary, and nothing else.

Checks made before collection (30 September 2026):

| Check | Finding |
| --- | --- |
| `api.transparency.dpwh.gov.ph/robots.txt` | HTTP 404, no crawler rules published for the API host |
| `transparency.dpwh.gov.ph/robots.txt` | Written for search engines; `/api/` is listed under private/admin areas of the portal host and `?page=` under duplicate-content rules; crawl delay 0.1 s |
| Terms of use or data policy | None published on the portal |
| Bot protection | Cloudflare is present; requests with our identifying user-agent were not challenged |

How the extractor behaves:

- reads and obeys `robots.txt` for every host it contacts, with wildcard support;
- identifies itself with a descriptive user-agent and a contact address taken from `.env`;
- sends one request at a time, at least 1.5 seconds apart (15 times slower than the portal's stated crawl delay);
- retries a limited number of times on HTTP 429 and 5xx, honouring `Retry-After`;
- stops immediately, without retrying, on HTTP 401 or 403 or any bot-protection challenge, and never attempts to bypass one;
- stores every response byte-for-byte in the raw layer with a SHA-256 manifest, and never overwrites a stored file.

## Extraction (Milestone 2)

```bash
python -m src.cli extract --max-pages 3
python -m src.cli extract --resume <run_id>
```

Each run writes to `data/raw/run_id=<UTC timestamp>/`:

| File | Content |
| --- | --- |
| `stats.json` | Portal summary counts, used later to reconcile totals |
| `projects/page_00001.json`, ... | Listing responses exactly as received |
| `manifest.jsonl` | One line per stored file: URL, HTTP status, size, SHA-256, UTC fetch time |
| `run.json` | Run metadata: status, stop reason, reported totals, request counts, robots.txt results |

A full run is about 5,300 requests and takes roughly two and a half hours. An interrupted or capped run is continued with `--resume`, which verifies every stored file against the manifest and fetches only the missing pages.

## Repository layout

| Path | Purpose |
| --- | --- |
| `config/settings.yml` | Non-secret pipeline defaults |
| `.env.example` | Template for environment-specific values (copy to `.env`, never commit) |
| `src/cli.py` | Unified command line entry point |
| `src/config.py` | Settings and environment loading |
| `src/extract`, `transform`, `load`, `validate`, `benchmark` | Pipeline modules (filled in by later milestones) |
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
