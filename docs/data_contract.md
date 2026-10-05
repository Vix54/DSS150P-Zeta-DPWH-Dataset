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

Any unparseable value in a date or timestamp column produces `Q_BAD_DATE:<source column>`. Values that carry a timezone offset are converted to UTC.

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

The source writes joint ventures as `NAME A (id) / NAME B (id)`. Each member becomes one row.

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
| `staged_at_utc` | timestamp (UTC) | Lineage, as in the contracts table |

## Quarantine table

Rejected rows are written with every source column exactly as read from the raw file, plus:

| Column | Content |
| --- | --- |
| `error_codes` | Quarantine codes for the row joined with `|` |
| `source_name` | `bettergov_hf` |
| `raw_run_id` | Raw run the row came from |
| `quarantined_at_utc` | When the staging run started |

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
| `W_DUPLICATE_COLUMN_MISMATCH:<column>` | Warning | A dropped duplicate column disagrees with its original |
| `W_BAD_BOOLEAN:<column>` | Warning | Boolean value not recognised |
| `W_CONTRACTOR_*`, `W_WINNER_NAMES_*` | Warning | See the contractor checks above |
