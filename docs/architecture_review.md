# Technical Architecture Review

**Project:** Group Zeta DPWH Infrastructure Pipeline  
**Scope:** Evaluation of the architectural criteria set forth in Section 8 of the Modular Data Pipeline Specification, benchmarked against pipeline tag `v0.4.0` and real-data executions recorded in `docs/evidence/20`–`31`.

---

## 1. Deterministic Hashing Mechanics

**Context:** Analysis of the necessity of `record_hash` for idempotent reruns, the imperative exclusion of volatile audit fields, and the formal inclusion of upstream source version markers.

**Technical Implementation:** The `curate` execution unit generates `record_hash` by calculating the SHA-256 digest across curated business columns strictly arranged in deterministic order (`src/transform/curated.py`). Within this calculation, null values are coerced to empty strings, date values are normalized to ISO-8601 formatting, and numeric scalars are converted to a fixed, full-precision text representation. Subsequently, the downstream relational loader evaluates this generated digest against the persisted hash, applying an update operation solely when variance is detected (`ON CONFLICT (contract_id) DO UPDATE ... WHERE record_hash IS DISTINCT FROM EXCLUDED.record_hash`).

**Architectural Justification:** In the absence of a deterministic row fingerprint, re-execution leaves only two substandard choices:
1. Re-executing blanket updates across the entire table volume (converting an otherwise no-op run into 248,418 artificial write events and completely invalidating the audit ledger).
2. Constructing multi-column equality evaluations natively in SQL, which introduces substantial query latency and remains prone to null-handling discrepancies and floating-point comparison errors.

Hence, deterministic hashing reduces the complex proposition of contract state drift into a single string comparison. Notably, live data execution demonstrated this efficiency, wherein the secondary load reported:
```
inserted=0, updated=0, unchanged=248418
```
*(Reference: `docs/evidence/23`)*

**Exclusion of Volatile Audit Fields:** System metadata fields—specifically `pipeline_run_id`, `processed_at_utc`, `staged_at_utc`, `_ingested_at_utc`, `raw_run_id`, and `warning_codes`—capture execution lineage rather than intrinsic contract domain attributes. For this reason, including any execution-specific marker in the hash digest would cause every rerun to generate novel fingerprints across all rows, completely bypassing the idempotency barrier. Moreover, empirical validation confirmed this isolation: `curate --rebuild` produced an updated curated Parquet file bearing a modified `processed_at_utc` and a distinct file checksum; however, the downstream database load reported `inserted=0, updated=0`, because the business values, and therefore the record hashes, were unchanged *(Reference: `docs/evidence/28`)*.

**Inclusion of Version Markers:** The specification includes `source_updated_at` so that a newer version of a record is recognized as a change even when its other attributes look identical. Considering that the upstream DPWH data asset lacks row-level update timestamps, the pipeline substitutes `source_revision` (the immutable Hugging Face commit hash). Consequently, a trade-off is deliberately accepted: the ingestion of an updated upstream release triggers a single hash invalidation across all records, executing a complete state refresh that explicitly binds every record to its authoritative upstream commit.

---

## 2. Raw Immutability and Downstream Resilience

**Context:** Evaluation of systemic risks associated with purging or mutating raw data layers post-transformation, regardless of curated layer compliance.

**Enforcement Protocol:** Ingestion routines within `extract-file` mirror source files byte-for-byte into isolated directories structured as `data/raw/source=<name>/run_id=<id>/`. The ingestion logic explicitly rejects overwrite attempts on existing target artifacts and records the resulting SHA-256 digest in `manifest.jsonl`. Crucially, incoming payloads must reconcile with the publisher's stated cryptographic digest (BetterGov) or the download registration hash (PSGC) prior to persistence. All downstream transformation steps mandate manifest verification prior to initiating read operations.

**Observed and Mitigated Risk Scenarios:**
* **Downstream Rule Evolution:** During initial staging, 9 records were quarantined due to the appearance of an undocumented alternative date format. Because raw inputs remained pristine and immutable, the parser rule was corrected and staging re-executed from the untouched raw copy. Staging quarantine counts dropped to 0, while 632 placeholder dates were successfully identified. Had the raw data been overwritten, the original textual strings would have been lost.
* **Irreversible Algorithmic Errors:** Historical issues surrounding duplicated contractor records and delay calculation boundaries were corrected through downstream clean rebuilds. Writing back into the raw landing layer would have embedded those analytical anomalies irreversibly into the source of truth.
* **Upstream Volatility and Availability:** Public DPWH API access remains constrained by Cloudflare bot-mitigation controls (as evidenced by four automated extraction passes terminating on HTTP 403 Forbidden). Thus, the local immutable raw copy represents an irreplaceable asset that cannot be assumed retrievable on demand.
* **Schema Expansion:** Nested structures—such as `bidders`, `components`, and `coordinates`—are currently unconsumed by downstream curated analytical models. Retaining the raw layer byte-for-byte preserves these fields for future analytical requirements.
* **Auditability and Verification:** Downstream layers must remain reconcilable against what the publisher released. Each run of `validate` performs this reconciliation, anchoring validation metrics back to the raw manifests and the expected publisher digests *(Reference: `docs/evidence/24`)*.

**Empirical Validation:** A clean-room reconstruction pass vacated the `data/raw`, `staging`, `curated`, `quarantine`, and `partitioned` directories, followed by an end-to-end rebuild from the two external source files. The process reproduced identical raw file hashes and identical row counts in every layer *(Reference: `docs/evidence/22`)*. Downstream file checksums differ between rebuilds because each file records its own run timestamps, which is why `record_hash` excludes those fields.

---

## 3. Failure Classification: Data-Quality versus Operational Faults

**Context:** Methodological separation between non-blocking row-level data quality quarantines and fatal system-level operational disruptions.

**Data-Quality Faults:** Characterized as row-level data violations that do not compromise pipeline infrastructure. These anomalies are systematically isolated into `data/quarantine/` accompanied by explicit diagnostic codes, enabling parent process execution to complete with exit code 0:

| Pipeline Stage | Error Signatures | Production Volume |
| :--- | :--- | :--- |
| **Staging** | `Q_MISSING_CONTRACT_ID`, `Q_DUPLICATE_CONTRACT_ID`, `Q_BAD_DATE:<column>` | 0 rows |
| **Curated** | `Q_PROGRESS_OUT_OF_RANGE`, `Q_ORPHAN_CONTRACT_ID` | 3 rows |
| **Database Load** | `Q_LOAD_NEGATIVE_PROJECT_COST`, `Q_LOAD_MISSING_STATUS` | 0 rows |

Moreover, records exhibiting structurally sound but unusual traits are preserved within the main analytical stream, flagged with non-quarantining `W_*` warning markers to maintain comprehensive reconciliation totals.

**Operational Failures:** Characterized as systemic disruptions affecting environment integrity, transport layers, or storage consistency. Operational faults trigger immediate exceptions, force non-zero process exits (exit code 1), and abort subsequent downstream processing:
* Cryptographic checksum mismatches
* Schema drift (missing or unexpected columns)
* Layer-to-layer reconciliation imbalances
* Database connection timeouts and transport drops
* Task execution exceeding its configured execution timeout

Within the orchestration framework (Airflow), operational exceptions trigger task-level failures followed by two retry attempts via exponential backoff. Upon final exhaustion, task failure callbacks log DAG run context, try counters, and full execution traces.

**Practical Demonstration:** The first capture of evidence 20 encountered a database validation failure because the PostgreSQL container had not completed initialization. This represented a transient infrastructure failure rather than a data integrity fault. Rerunning the check after container health confirmation (`docker compose up -d --wait`) resolved the failure, which is the behaviour a retry-with-backoff routine automates.

---

## 4. Columnar versus Row-Oriented Storage Benchmarks

**Context:** Structural mechanisms driving the performance differential between Apache Parquet and CSV/JSON serialization over selective analytical workloads.

**Empirical Benchmarks:** Conducted across 248,418 records $\times$ 43 attributes, calculating median latency over 5 iterations filtering for `status_name = 'On-Going'` (34,728 matching records) *(Reference: `docs/evidence/26`)*:

| Format / Engine | Storage Footprint (MB) | Full Scan Latency (s) | Filtered Query Latency (s) |
| :--- | :--- | :--- | :--- |
| **CSV** | 219.8 MB | 3.12 s | 3.26 s |
| **JSON Lines** | 415.0 MB | 5.85 s | 5.75 s |
| **Parquet (Snappy)** | 49.4 MB | 0.12 s | 0.10 s |
| **Parquet (Zstandard)** | 33.9 MB | 0.10 s | 0.09 s |
| **PostgreSQL** | 226.1 MB | 3.65 s | 0.54 s |

**Underlying Mechanisms:**
* **Columnar Layout Projection:** Values within a specific column are stored contiguously on disk. Query engines project only the required attribute vectors while skipping unreferenced columns entirely. Conversely, CSV serialization mandates streaming and parsing every byte across all columns.
* **Dictionary Encoding:** Low-cardinality nominal values (e.g., `status_name` with 5 distinct labels; `region` with 18 distinct labels) are substituted with compact integer representations alongside an index dictionary. Consequently, filtering operations evaluate scalar integers rather than string sequences.
* **Native Binary Types:** Parquet stores numerical scalars and timestamps in binary formats, bypassing the textual parsing that accounts for much of CSV deserialization time.
* **Metadata Statistics and Row-Group Pruning:** Parquet embeds column-level min/max summary statistics within row-group footers, letting readers skip row groups that cannot match. Because this benchmark file is sorted by `contract_id`, "On-Going" records are spread across all row groups and little is skipped here. The partitioned copy (`start_year`, `start_month`) adds a coarser form of pruning: a date-filtered query can skip whole partition folders.
* **High-Ratio Compression:** Zstandard achieved superior compaction (yielding an artifact roughly 15% of the baseline CSV size) and read slightly faster than Snappy, as less data had to be read from disk.

Notably, PostgreSQL exhibited the highest write time and a footprint larger than the CSV, yet its filtered query ran about 7 times faster than its full table read. The benchmark table has no index, so the gain comes from the database returning only the 34,728 matching tuples over the connection instead of all 248,418.

---

## 5. Scheduler Decoupling and CLI Encapsulation

**Context:** Operational hazards of housing data transformation logic directly within workflow definition DAGs rather than invoking discrete CLI entry points.

**Architecture Implemented:** Every orchestration task in `dags/dss150p_pipeline.py` is configured as a `BashOperator` invoking the pipeline CLI via `python -m src.cli <command>`. DAG scripts are restricted strictly to topology definitions, scheduling intervals, retry parameters, execution timeouts, and run identity propagation.

**Mitigated Systemic Hazards:**
* **DAG Parser Collapse:** Airflow dynamically evaluates DAG files at regular intervals. Encapsulating domain logic or complex dependencies within DAG scripts causes parse-time failures, dropping the entire workflow from the UI. An earlier iteration of the team's DAG referenced a task variable and an import that did not exist; the file failed to parse, disabling pipeline visualization and monitoring.
* **Dependency Isolation:** Airflow 2.10.5 pins internal constraints (SQLAlchemy 1.4.54, pandas 2.1.4), whereas the pipeline codebase requires modern data libraries (SQLAlchemy 2.1.1, pandas 3.0.6). CLI execution decouples the runtime environment, allowing the pipeline to execute in an isolated virtual environment inside the Airflow container (`/opt/pipeline-venv`); the container's task logs show every task running `/opt/pipeline-venv/bin/python -m src.cli <command>` (*Reference: `docs/evidence/31`*).
* **Testability Beyond Orchestration:** Embedding transformations within DAG files restricts testing strictly to the scheduler harness. Delegating processing to the CLI allows 105 automated tests to validate operations locally without Airflow, and the same commands run unchanged by hand, in the pipeline container, or under Airflow.
* **Parse-Time Resource Bottlenecks:** Heavy module imports and data reads at the top level of DAG files execute on every scheduler parsing loop, degrading scheduler responsiveness.
* **Implementation Divergence:** Separate DAG implementations risk logic drift between ad-hoc local executions and production schedules. Single-entry CLI wrappers ensure execution symmetry across all runtime environments.

---

## 6. Retry Interactions and Idempotent Upserts

**Context:** Structural mechanisms preventing data duplication, race conditions, or partial write states during worker crashes and subsequent scheduler retries.

**Systemic Risks:** Retries executing non-idempotent operations create data anomalies. A standard `INSERT` interrupted after commit but prior to network confirmation will duplicate records upon retry. Similarly, executing `TRUNCATE` and reload across split transactions risks leaving empty downstream relations if an unhandled termination occurs mid-cycle.

**Enforced Mitigation Controls:**
* **Deterministic Execution Identity:** Every task receives a run identifier formatted as `PIPELINE_RUN_ID=airflow__{{ ts_nodash }}`, ensuring static lineage across retries of the same DAG run.
* **Short-Circuiting on Unchanged Input:** `extract-file` detects a pre-existing raw run matching the input SHA-256 and avoids duplicate writes. Downstream transformation stages (`stage`, `curate`) report an "Unchanged" state when output for the same input run already exists, and `partition` does so when the curated file's checksum is unchanged.
* **Atomic File System Swaps:** Staging, curated, quarantine and partition outputs are written into temporary `.part` directories, swapping to production targets only upon successful completion. A process crash therefore leaves no partial target files.
* **Single-Transaction Upsert Guarantees:** Relational updates load the batch into a temporary staging relation, executing the final merge (`ON CONFLICT (contract_id) DO UPDATE ... WHERE record_hash IS DISTINCT FROM EXCLUDED.record_hash`) within a single atomic database transaction. Any failure before commit triggers an automatic rollback, and the primary key makes a duplicate contract impossible.
* **Granular Audit Trails:** `load-partition` logs every execution attempt—including failures—to `audit.partition_loads`, ensuring retry histories remain fully observable.

**Empirical Validation:** Secondary load passes over verified inputs consistently yielded zero insertions and zero updates (`inserted=0, updated=0`, *Reference: `docs/evidence/23`*). Rebuilding downstream layers following deliberate file corruption similarly converged on a `0/0` operational delta (*Reference: `docs/evidence/28`*). The DAG's `controlled_failure` parameter fails `validate` on its first try so that the automatic retry and recovery can be observed: in the Airflow container, `validate` failed on attempt 1, was retried automatically, and passed on attempt 2 with 0 failed checks (*Reference: `docs/evidence/31`, `docs/images/airflow_03_validate_try1.png`, `airflow_04_validate_try2.png`*).

**Known Limitation:** If `extract-file` were interrupted after creating a raw run directory but before writing its manifest, a retry under the same run identifier would find the directory and stop rather than resume. Clearing or resuming an incomplete raw run would close this gap; it has not occurred in practice.

---

## 7. Partition Granularity and Cardinality Trade-Offs

**Context:** Analytical evaluation of partitioning schemes, balancing directory metadata overhead against large-scale read efficiency.

**Selected Partition Strategy:** The partition routine divides the primary table along temporal dimensions:  
`data/partitioned/dpwh_contracts/start_year=YYYY/start_month=M/`  
This produces 125 partitions across the 248,418 contracts, averaging roughly 2,000 records per file. Contracts lacking confirmed project start dates (7,669 records) are systematically routed to an explicit `__HIVE_DEFAULT_PARTITION__` directory.

**Overheads of Extreme Over-Partitioning:**
* **File Handle and Metadata Explosion:** Partitioning on fine-grained fields like `contract_id` or individual calendar days generates hundreds of thousands of independent files. File footers, embedded schemas, and file system metadata consume disproportionate storage relative to actual row payloads.
* **Encoding Degradation:** Dictionary encoding and compression operate within each row group. Splitting rows into microscopic chunks reduces categorical repetition, degrading compression ratios.
* **Planning Bottlenecks:** Query planners must execute recursive directory traversals and read individual Parquet footers across thousands of paths, causing planning latency to exceed data extraction time.
* **Composite Key Multiplication:** Multi-dimensional partitioning (e.g., `region` $\times$ `category` $\times$ `month`) produces high-cardinality combinatorial trees populated primarily by near-empty or empty partitions.

**Under-Partitioning Constraints:** Conversely, storing all records in an unpartitioned file requires full table scans for single-month queries. Partitioning by year and month maintains partition sizes that are sufficiently large to optimize columnar compression while sufficiently granular to allow selective queries and partition-level loads.

---

## 8. Incremental Architecture Migration Pathways

**Context:** Architectural adjustments required to migrate the current static batch ingestion model toward paginated REST APIs or Change Data Capture (CDC) streams.

**Paginated REST API Extraction:** The project's polite extractor (`src/extract/dpwh_projects.py`) outlines the necessary pattern:
1. Adherence to `robots.txt` directives.
2. Explicit client User-Agent identification.
3. Paced requests (1.5-second minimum intervals).
4. Exponential backoff bounded strictly to 429 and 5xx responses.
5. Immediate termination upon encountering 401, 403, or bot challenge pages.
6. Byte-for-byte page persistence with independent manifest entries.

Unlike static batches, API pagination introduces non-atomic snapshots: state drift can occur between page 1 and page 5,000. Hence, extraction runs require static run boundaries and baseline reconciliation checks against the portal's public summary endpoint (`/ai/stats`).

**Incremental State Hydration:** Implementing differential record capture requires upstream watermarking (e.g., `updated_at`). Because the DPWH portal lacks row-level modification timestamps, complete batch ingestion reconciled via `record_hash` remains the only robust pattern. Were a reliable watermark introduced, the staging layer would filter for the latest state per business key, while the downstream `record_hash` guard would preserve row-level update idempotency.

**Streaming and Change Data Capture (CDC) Architecture:**
* **Event Ordering and Idempotency:** Distributed event streams risk out-of-order delivery and duplication. Events must carry sequential log identifiers or transaction timestamps, enforcing idempotent upserts over target tables.
* **Tombstone Processing:** The current batch pipeline executes upsert flows without deletes. Streaming feeds must incorporate soft-delete flags or explicit tombstone markers to reconcile downstream purges.
* **Schema Evolution:** Batch runs fail fast upon detecting schema drift. In contrast, streaming ingestion requires versioned schemas and schema registries to handle backward-compatible additions without interrupting stream consumption.
* **Continuous Validation Metrics:** Validation routines must pivot from whole-file integrity audits to continuous windowed health checks: streaming throughput, consumer group lag, sequence gap detection, and periodic reconciliation against raw ledger stores.

**Ethical Invariant:** Regardless of ingestion mechanics—whether batch, paginated API, or streaming CDC—the data pipeline must operate solely through authorized endpoints. When encountering access controls, the pipeline must halt rather than attempt bypass techniques.
