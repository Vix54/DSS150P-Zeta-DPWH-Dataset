# DSS150P Zeta: DPWH Modular Data Pipeline

Group Zeta's modular, rerun-safe data pipeline for DPWH infrastructure project data, built to the course's Modular Data Pipeline Specification (Python, PostgreSQL, Parquet, Docker Compose, Apache Airflow).

**Status:** Milestone 1 in progress (reproducible environment and configuration).

## Data source and scraping ethics

The data comes from the public DPWH transparency portal (`transparency.dpwh.gov.ph`). The scraper will:

- follow the portal's `robots.txt` and stay off every disallowed path, including `/api/`;
- identify itself with a descriptive user-agent and a contact address taken from `.env`;
- wait at least 1.5 seconds between requests and back off on errors;
- stop, and never attempt to bypass, any bot-protection challenge;
- collect a limited, documented subset of projects rather than the full portal.

Scraper details will be added once Milestone 2 begins.

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

## Commit and tag conventions

- Commit messages: `Lastname - Action - module - short description`, for example `Risma - Fix - extract - recreated code structure`.
- Work happens on short-lived branches; `main` stays clean.
- Milestone breakthroughs are marked with semantic tags (`v0.1.0`, `v0.2.0`, ...).
