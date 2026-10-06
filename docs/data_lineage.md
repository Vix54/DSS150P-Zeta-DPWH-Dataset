# Data Lineage

**Project:** Group Zeta DPWH Infrastructure Pipeline

How data moves from the two source files to the database, with the CLI command that performs each step and where rejected rows go. Every arrow is a `python -m src.cli` command; Airflow only runs those commands in this order.

```mermaid
flowchart TD
    subgraph Sources["Sources (downloaded by hand into data/source/)"]
        S1["BetterGov.ph release on Hugging Face<br/>dpwh_transparency_data_all_details.parquet<br/>CC0, revision 648ea96"]
        S2["PSA PSGC as of 30 June 2026<br/>PSGC-2Q-2026-Publication-Datafile.xlsx<br/>CC BY 4.0"]
    end

    subgraph Raw["Raw (byte-for-byte, SHA-256 manifest)"]
        R1["data/raw/source=bettergov_hf/run_id=.../"]
        R2["data/raw/source=psa_psgc/run_id=.../"]
    end

    subgraph Staging["Staging"]
        ST["data/staging/source=bettergov_hf/run_id=.../<br/>contracts.parquet, contract_contractors.parquet"]
    end

    subgraph Curated["Curated"]
        C["data/curated/run_id=.../<br/>dpwh_contracts.parquet, contract_contractors.parquet, psgc_regions.parquet"]
    end

    subgraph Serving["Serving"]
        P["data/partitioned/dpwh_contracts/<br/>start_year=YYYY/start_month=M/"]
        DB[("PostgreSQL<br/>curated.dpwh_projects")]
        AU[("PostgreSQL<br/>audit.partition_loads")]
        B["data/benchmarks/"]
    end

    subgraph Quarantine["Quarantine (rows with error codes)"]
        Q1["data/quarantine/source=bettergov_hf/<br/>unparseable or duplicate rows"]
        Q2["data/quarantine/curated/<br/>progress outside 0-100, orphan members"]
        Q3["data/quarantine/load/<br/>rows the table rejects, e.g. negative cost"]
    end

    S1 -->|"extract-file (hash must match)"| R1
    S2 -->|"extract-file (hash recorded at download)"| R2
    R1 -->|"stage: typing, date rules, contractor parsing"| ST
    R1 -.-> Q1
    ST -->|"curate: metrics, delay flag, record_hash"| C
    R2 -->|"curate: region check, flag only"| C
    ST -.-> Q2
    C -->|"partition"| P
    C -->|"load: hash-guarded upsert"| DB
    P -->|"load-partition"| DB
    P -->|"load-partition logs each attempt"| AU
    C -.-> Q3
    C -->|"benchmark"| B
```

`validate` reads every layer back: raw files against `manifest.jsonl`, each layer's outputs against their recorded SHA-256 and row counts, row reconciliation from raw to partitions, the curated calculations, and the database hashes against the curated file.
