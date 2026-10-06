# Data contract: staging layer

This contract defines what the staging step (`python -m src.cli stage`) accepts from the raw layer, what it produces, and which rules send a row to quarantine or attach a warning. It applies to the primary source `bettergov_hf` (see `docs/sources.md`).

## Principles

- **The raw layer is never changed.** Staging reads a verified raw run and writes new files.
- **Every input row is accounted for.** Rows in = rows staged + rows quarantined; the run stops if this does not hold.
- **Quarantine only what cannot be used.** A row goes to quarantine only when it is structurally broken: no contract ID, a duplicated contract ID, or a number or date that cannot be parsed. Rows that are unusual but usable stay in staging with warning codes, so they still count in reconciliation.
- **No speculative business rules.** Values are typed and normalised, never corrected or inferred. Ambiguous source fields are kept as published and documented here.
- **Schema drift stops the run.** If the raw file has any column missing or any column not listed in this contract, staging stops and asks for this contract to be updated first.

## Inputs and outputs

| Item | Location |
| --- | --- |
| Input | `data/raw/source=bettergov_hf/run_id=<run>/` (latest run by default, checksum verified before reading) |
| Staged contracts | `data/staging/source=bettergov_hf/run_id=<run>/contracts.parquet` (one row per contract, sorted by `contract_id`) |
| Contractor members | `data/staging/source=bettergov_hf/run_id=<run>/contract_contractors.parquet` (one row per contractor on a contract) |
| Report | `data/staging/source=bettergov_hf/run_id=<run>/staging_report.json` (row counts, rule counts, status counts, output checksums) |
| Quarantine | `data/quarantine/source=bettergov_hf/run_id=<run>/contracts.parquet` (rejected rows exactly as read from raw, plus `error_codes`) |

Staging output is keyed to the raw run it came from. Running the step again for the same raw run does nothing unless `--rebuild` is given; a rebuild replaces the output folders atomically.

## Normalisation applied to every text field

Leading and trailing spaces are removed and empty strings become null. Case and inner spacing are kept exactly as published.

## Contracts table (`contracts.parquet`)

### Identity and description

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `contract_id` | string | `contractId` | Required and unique. Null or blank: `Q_MISSING_CONTRACT_ID`. Appears more than once: every copy gets `Q_DUPLICATE_CONTRACT_ID` |
| `contract_name` | string | `contractName` | Text normalisation |
| `description` | string | `description` | Text normalisation |
| `category` | string | `category` | Text normalisation |
| `infra_type` | string | `infraType` | Text normalisation |
| `status` | string | `status` | Expected values: Completed, On-Going, For Procurement, Terminated, Not Yet Started. Null: `W_STATUS_MISSING`. Other value: `W_STATUS_UNKNOWN` |
| `procurement_status_code` | string | `status_1` | Kept as published (F, A, CP, T, ANV, FNV, TNV, C were observed). The source does not document these codes, so they are not interpreted |
| `nys_reason` | string | `nysReason` | Text normalisation |
| `region` | string | `region` | Text normalisation |
| `implementing_office` | string | `province` | Renamed because the source field holds the implementing district engineering office (for example "Quezon 2nd DEO"), not a province |
| `infra_year` | integer | `infraYear` | Published as text. Not a whole number: `Q_BAD_INTEGER:infraYear` |
| `program_name` | string | `programName` | Text normalisation |
| `source_of_funds` | string | `sourceOfFunds` | Text normalisation |
| `funding_instrument` | string | `fundingInstrument` | Text normalisation |

### Amounts and progress

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `budget_php` | decimal | `budget` | Kept as reported. In the January 2026 release it equals `abc` in about 10% of rows and `awardAmount` in about 50%, so its meaning is not consistent; it is not used to derive other values |
| `abc_php` | decimal | `abc` | Approved budget for the contract. Published as text; commas removed. Unparseable: `Q_BAD_NUMBER:abc` |
| `award_amount_php` | decimal | `awardAmount` | Published as text; commas removed. Unparseable: `Q_BAD_NUMBER:awardAmount` |
| `amount_paid_php` | decimal | `amountPaid` | Zero in every row of the January 2026 release; kept but excluded from metrics |
| `progress_pct` | decimal | `progress` | Outside 0 to 100: `W_PROGRESS_RANGE` |

Any unparseable value in a numeric column produces `Q_BAD_NUMBER:<source column>`. A negative amount produces `W_NEGATIVE_AMOUNT:<column>`.

### Dates

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `start_date` | date | `startDate` | Unparseable: `Q_BAD_DATE:startDate` |
| `completion_date` | date | `completionDate` | Unparseable: `Q_BAD_DATE:completionDate`. Earlier than `start_date`: `W_DATE_ORDER:completion_date` |
| `contract_effectivity_date` | date | `contractEffectivityDate` | Unparseable: `Q_BAD_DATE:contractEffectivityDate` |
| `expiry_date` | date | `expiryDate` | Unparseable: `Q_BAD_DATE:expiryDate`. Earlier than `contract_effectivity_date`: `W_DATE_ORDER:expiry_date` |
| `advertisement_date` | timestamp | `advertisementDate` | Published with seven decimal places of seconds; trimmed to microseconds. Timezone not stated by the source; stored as published |
| `bid_submission_deadline` | timestamp | `bidSubmissionDeadline` | As above |
| `date_of_award` | timestamp | `dateOfAward` | As above |

Date and timestamp parsing applies to every column above, in this order:

1. ISO format (for example `2022-02-24` or `2021-10-27 00:00:00.0000000`). Values that carry a timezone offset are converted to UTC.
2. If ISO parsing fails, the second format seen in the source, `MM/DD/YYYY hh:mm:ss AM/PM` (for example `12/09/2025 12:00:00 AM`, read as 9 December 2025). The row gets `W_DATE_FORMAT_ALT:<source column>` so the month-first reading stays traceable. In the January 2026 release this format appears only in 9 For Procurement rows from one office (`25FI0067` to `25FI0076`).
3. Any value on 1 January 1900 is a placeholder for "no date". It becomes null and the row gets `W_PLACEHOLDER_DATE:<source column>`.
4. A value that still cannot be parsed produces `Q_BAD_DATE:<source column>`.

### Location

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `latitude` | decimal | `latitude` | Only one of latitude and longitude present: `W_COORDINATES_PARTIAL`. Both present but outside latitude 4.0 to 21.5 or longitude 116.0 to 127.0: `W_COORDINATES_OUTSIDE_PH` |
| `longitude` | decimal | `longitude` | As above |

### Contractor

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `contractor_raw` | string | `contractor` | Kept as published; parsed into `contract_contractors.parquet` |
| `winner_names_raw` | string | `winnerNames` | Kept as published (empty becomes null); used only for the consistency check below |
| `contractor_count` | integer | derived | Number of contractors parsed from `contractor_raw` |
| `is_joint_venture` | boolean | derived | True when `contractor_count` is greater than 1 |

Contractor checks (warnings only):

| Code | Condition |
| --- | --- |
| `W_CONTRACTOR_MISSING` | No contractor and status is not For Procurement |
| `W_WINNER_NAMES_WITHOUT_CONTRACTOR` | No contractor but `winnerNames` is filled |
| `W_WINNER_NAMES_MISSING` | Contractor present but `winnerNames` is empty |
| `W_WINNER_NAMES_MISMATCH` | `winnerNames` differs from the names rebuilt from `contractor` (member names without their IDs, sorted ignoring punctuation and spaces, joined with ", ") |
| `W_CONTRACTOR_ID_MISSING` | At least one member has no trailing registration ID |
| `W_CONTRACTOR_NAME_TRUNCATED` | At least one member name has unbalanced parentheses because the source cut it short |
| `W_CONTRACTOR_DUPLICATE_MEMBER` | The same member appears more than once in `contractor`; it is counted once |

### Bidders, components and coordinates

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `bidders_json` | string (JSON) | `bidders` | Nested list kept losslessly as JSON text; split into tables in the curated layer once the fields are profiled |
| `bidder_count` | integer | derived | Number of entries in `bidders` |
| `components_json` | string (JSON) | `components` | As above |
| `component_count` | integer | derived | Number of entries in `components` |
| `coordinates_json` | string (JSON) | `coordinates` | As above |
| `coordinate_count` | integer | derived | Number of entries in `coordinates` |

### Document links

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `advertisement_url` | string | `advertisement` | Stored as text, never fetched |
| `contract_agreement_url` | string | `contractAgreement` | As above |
| `notice_of_award_url` | string | `noticeOfAward` | As above |
| `notice_to_proceed_url` | string | `noticeToProceed` | As above |
| `program_of_work_url` | string | `programOfWork` | As above (empty in the observed rows) |
| `engineering_design_url` | string | `engineeringDesign` | As above (empty in the observed rows) |

### Verification, images and livestream

| Column | Type | Source column | Rule |
| --- | --- | --- | --- |
| `is_verified` | boolean | `verified` | Unrecognised value: null and `W_BAD_BOOLEAN:verified` |
| `is_verified_by_dpwh` | boolean | `isVerifiedByDpwh` | As above |
| `is_verified_by_public` | boolean | `isVerifiedByPublic` | As above |
| `total_images` | integer | `totalImages` | Not a whole number: `Q_BAD_INTEGER:totalImages` |
| `has_images` | boolean | `hasImages` | As `is_verified` |
| `latest_image_at` | timestamp | `latestImageDate` | As `advertisement_date` |
| `is_live` | boolean | `isLive` | Volatile; excluded from the curated `record_hash` |
| `livestream_url` | string | `livestreamUrl` | Volatile; excluded from the curated `record_hash` |
| `livestream_video_id` | string | `livestreamVideoId` | Volatile; excluded from the curated `record_hash` |
| `livestream_detected_at` | timestamp | `livestreamDetectedAt` | Volatile; excluded from the curated `record_hash` |

### Lineage and warnings

| Column | Type | Content |
| --- | --- | --- |
| `warning_codes` | string | Warning codes for the row joined with `|`, null when there are none |
| `source_name` | string | `bettergov_hf` |
| `source_revision` | string | Dataset revision recorded at ingestion |
| `raw_run_id` | string | Raw run the row came from |
| `pipeline_run_id` | string | Orchestration run that produced the row: `PIPELINE_RUN_ID` from the environment (set by Airflow), otherwise `manual__<UTC timestamp>` |
| `_ingested_at_utc` | timestamp (UTC) | When the raw file was ingested |
| `staged_at_utc` | timestamp (UTC) | When this staging run started |

### Source columns dropped in staging

| Source column | Reason |
| --- | --- |
| `infraType_1` | Identical to `infraType` in every row of the January 2026 release. Any row where it differs gets `W_DUPLICATE_COLUMN_MISMATCH:infraType_1` |
| `latitude_1` | Identical to `latitude`; same check |
| `longitude_1` | Identical to `longitude`; same check |
| `winnerNames` | Kept as `winner_names_raw` for checking only; the parsed members replace it |

## Contractor members table (`contract_contractors.parquet`)

The source writes joint ventures as `NAME A (id) / NAME B (id)`. Each member becomes one row. When the same member (same name and ID) is repeated in one contract (34 contracts in the January 2026 release), for example `PANAAD CONSTRUCTION (37345)` three times, it is kept once, positions are numbered after de-duplication, and the contract gets `W_CONTRACTOR_DUPLICATE_MEMBER`. The source's own `winnerNames` field lists such members once, which supports this rule. `contractor_count` and `is_joint_venture` use the de-duplicated members.

| Column | Type | Content |
| --- | --- | --- |
| `contract_id` | string | Contract the member belongs to |
| `member_position` | integer | Order in the source string, starting at 1 |
| `member_raw` | string | The member's text as published |
| `display_name` | string | Member text without the trailing ID; matches the form used in `winnerNames` |
| `contractor_name` | string | `display_name` without any former-name clause |
| `former_name` | string | Text inside `(FORMERLY: ...)`, `(PREVIOUSLY ...)` or `(FOR. ...)`, which the source sometimes cuts short |
| `contractor_source_id` | integer | Number in the trailing parentheses; null when absent |
| `has_revoked_marker` | boolean | True when the trailing parentheses read `[REVOKED] <id>`. Records only that the source shows this marker; its meaning is not interpreted |
| `name_truncated` | boolean | True when the name has unbalanced parentheses because the source cut it short; the text is never completed or guessed |
| `source_name` | string | Lineage, as in the contracts table |
| `raw_run_id` | string | Lineage, as in the contracts table |
| `pipeline_run_id` | string | Lineage, as in the contracts table |
| `staged_at_utc` | timestamp (UTC) | Lineage, as in the contracts table |

## Quarantine table

Rejected rows are written with every source column exactly as read from the raw file, plus:

| Column | Content |
| --- | --- |
| `error_codes` | Quarantine codes for the row joined with `|` |
| `source_name` | `bettergov_hf` |
| `raw_run_id` | Raw run the row came from |
| `pipeline_run_id` | Orchestration run that rejected the row |
| `quarantined_at_utc` | When the staging run started |

## Source field limits

Findings from staging the January 2026 release (raw run `20261005T154820Z`), recorded so that downstream users do not mistake them for pipeline errors:

| Finding | Evidence |
| --- | --- |
| Contractor member names are cut at 50 characters | 12,209 of the 12,818 members flagged `name_truncated` are exactly 50 characters long, or 49 when the cut fell on a space that was trimmed. The cut usually lands inside a `(FORMERLY ...)` clause |
| A longer limit of 100 characters is likely | The longest member name in the release is exactly 100 characters |
| Contracts with a contractor recorded more than once | 34 contracts repeat the same member; `winnerNames` lists each once |
| Two date formats | ISO in almost every row; `MM/DD/YYYY hh:mm:ss AM/PM` in 9 rows |
| 1 January 1900 used as "no date" | 446 `advertisementDate` and 186 `bidSubmissionDeadline` values, in both formats (including the 9 rows above) |
| Winner names that still disagree | 40 contracts where `winnerNames` differs from the rebuilt member names after de-duplication; not yet explained, kept as `W_WINNER_NAMES_MISMATCH` |

These limits come from the publisher's data, not from staging. Truncated names are never completed or guessed.

## Curated layer (`python -m src.cli curate`)

Curated reads one staging run (its checksums are verified first) and the latest raw run of the official PSGC reference, and writes:

| Item | Location |
| --- | --- |
| Curated contracts | `data/curated/run_id=<staging run>/dpwh_contracts.parquet` (one row per contract, sorted by `contract_id`) |
| Contractor members | `data/curated/run_id=<staging run>/contract_contractors.parquet` (members of curated contracts only) |
| Region reference | `data/curated/run_id=<staging run>/psgc_regions.parquet` (each DPWH region label, its PSGC match and contract count) |
| Report | `data/curated/run_id=<staging run>/curated_report.json` (row accounting, match counts, hashed columns, output checksums) |
| Quarantine | `data/quarantine/curated/run_id=<staging run>/contracts.parquet` and `contract_contractors.parquet` |

Every staged row is either curated or quarantined; the run stops if the counts do not add up. Curating the same staging run again does nothing unless `--rebuild` is given.

### Column names

Curated keeps the staging columns described above, with three renamed to match the database load and schema already in the repository:

| Staging column | Curated column |
| --- | --- |
| `status` | `status_name` |
| `budget_php` | `project_cost` (same caveat: the source `budget` is not consistently the ABC or the award amount) |
| `progress_pct` | `physical_accomplishment` |

Curated leaves out fields that are not used for analysis at this layer and stay available in staging: the nested JSON columns, document links, verification and image flags, `amount_paid_php` (zero in every row) and the volatile livestream fields.

### Added columns

| Column | Type | Rule |
| --- | --- | --- |
| `region_psgc_code` | string | 10-digit PSGC code of the matched region; null when there is no single match |
| `region_psgc_name` | string | Official PSGC region name |
| `region_matches_psgc` | boolean | True when the DPWH `region` label matches exactly one PSGC region |
| `award_savings_php` | decimal | `abc_php − award_amount_php` when both are present |
| `award_to_abc_pct` | decimal | `award_amount_php ÷ abc_php × 100` when both are present and `abc_php > 0`, rounded to 4 places |
| `is_delayed` | boolean | True when `status_name` is On-Going, `completion_date` is earlier than `delay_as_of_date`, and `physical_accomplishment` is below 100 |
| `delay_as_of_date` | date | The primary source's snapshot date (`revision_date_utc` of `bettergov_hf`, 22 January 2026). Using the snapshot date instead of today keeps the flag, and the record hash, identical on every rerun |
| `processed_at_utc` | timestamp (UTC) | When this curated run started |
| `record_hash` | string | SHA-256 fingerprint of the business columns (see below) |

### Region matching against the PSGC

The PSGC workbook's `PSGC` sheet is read and rows with `Geographic Level = Reg` form the official region list. A DPWH label matches a PSGC region when they share a key: the Roman-numeral form (`Region IV-A`), the name without its bracketed part (`National Capital Region`), or the bracketed abbreviation (`NCR`). Labels with no shared key are looked up in `curated.region_aliases` in `config/settings.yml`; the only alias is `Region IV-B → MIMAROPA`, the region's official name. Following the rule for supplementary sources, the PSGC only flags: an unmatched or ambiguous label keeps its row and adds `W_REGION_NOT_IN_PSGC` or `W_REGION_AMBIGUOUS_IN_PSGC` to `warning_codes`. "Central Office" (the DPWH head office, 297 contracts in the January 2026 release) is not a region and is expected to stay unmatched.

### Record hash

`record_hash` is the SHA-256 of the curated business columns, joined in a fixed order, with nulls as empty text, dates in ISO form and numbers in a fixed text form. It includes `source_revision` as the source version marker and leaves out execution metadata that changes on every run: `pipeline_run_id`, `processed_at_utc`, `staged_at_utc`, `_ingested_at_utc`, `raw_run_id` and `warning_codes`. The exact column list is written to `curated_report.json` under `record_hash_columns`. The database load can compare it with the stored hash and skip unchanged rows.

### Curated quarantine

| Code | Rows | Meaning |
| --- | --- | --- |
| `Q_PROGRESS_OUT_OF_RANGE` | Contracts | `physical_accomplishment` outside 0 to 100. Staging keeps these rows with `W_PROGRESS_RANGE`; curated quarantines them because the database schema enforces 0 to 100. Three rows in the January 2026 release (−100, −0.1, −0.02) |
| `Q_ORPHAN_CONTRACT_ID` | Contractor members | A member whose `contract_id` is not among the staged contracts |

Quarantined contracts keep every staged column plus `error_codes` and `quarantined_at_utc`.

## Code reference

| Code | Effect | Meaning |
| --- | --- | --- |
| `Q_MISSING_CONTRACT_ID` | Quarantine | Contract ID is null or blank |
| `Q_DUPLICATE_CONTRACT_ID` | Quarantine | Contract ID appears more than once |
| `Q_BAD_NUMBER:<column>` | Quarantine | Numeric value cannot be parsed |
| `Q_BAD_INTEGER:<column>` | Quarantine | Value is not a whole number |
| `Q_BAD_DATE:<column>` | Quarantine | Date or timestamp cannot be parsed |
| `W_STATUS_MISSING`, `W_STATUS_UNKNOWN` | Warning | Status is null or outside the expected values |
| `W_PROGRESS_RANGE` | Warning | Progress outside 0 to 100 |
| `W_NEGATIVE_AMOUNT:<column>` | Warning | Negative amount |
| `W_COORDINATES_PARTIAL`, `W_COORDINATES_OUTSIDE_PH` | Warning | Incomplete or out-of-country coordinates |
| `W_DATE_ORDER:<column>` | Warning | End date earlier than start date |
| `W_DATE_FORMAT_ALT:<column>` | Warning | Date read from the `MM/DD/YYYY hh:mm:ss AM/PM` format |
| `W_PLACEHOLDER_DATE:<column>` | Warning | Date was the 1 January 1900 placeholder and is stored as null |
| `W_DUPLICATE_COLUMN_MISMATCH:<column>` | Warning | A dropped duplicate column disagrees with its original |
| `W_BAD_BOOLEAN:<column>` | Warning | Boolean value not recognised |
| `W_CONTRACTOR_*`, `W_WINNER_NAMES_*` | Warning | See the contractor checks above |
