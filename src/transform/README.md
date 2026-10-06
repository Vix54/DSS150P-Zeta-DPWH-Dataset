# Transformation layers

Business rules live here and are run through the command line (`python -m src.cli stage` and `python -m src.cli curate`), so Airflow only has to call those commands. The full rules, column lists and error codes are in `docs/data_contract.md`.

## Staging (`staging.py`, `cleaning.py`, `contractors.py`)

- Reads the latest raw run of the BetterGov release after verifying its checksum; the raw file is never changed.
- Types and normalises every column: text trimmed, numbers and dates parsed, a second date format and the 1900-01-01 placeholder handled, nested lists kept as JSON text.
- Splits the contractor string into one row per joint-venture member, with contractor ID, former name, revoked marker and a flag for names the source cut short.
- Quarantines only rows that cannot be used (no or duplicate `contract_id`, unparseable numbers or dates) with error codes; unusual rows stay with warning codes. Values are never filled with zero, capped or silently dropped.
- Adds `pipeline_run_id`, `_ingested_at_utc` and `staged_at_utc`.

## Curated (`curated.py`)

- Reads one staging run (checksums verified) and the official PSGC reference.
- Uses the column names the database load expects: `project_cost`, `physical_accomplishment`, `status_name`, alongside `contract_id`, `start_date`, `infra_year` and `is_delayed`.
- Flags each contract's region against the PSGC without dropping rows.
- Adds award metrics (`award_savings_php`, `award_to_abc_pct`) and `is_delayed`, measured against the source snapshot date so reruns give identical results.
- Adds `processed_at_utc` and a deterministic `record_hash` for hash-guarded loading.
- Quarantines progress values outside 0 to 100 (which the database schema rejects) and orphan contractor rows.

For loading and partitioning, `src.transform.curated.curated_contracts_path(settings)` returns the latest `dpwh_contracts.parquet`.
