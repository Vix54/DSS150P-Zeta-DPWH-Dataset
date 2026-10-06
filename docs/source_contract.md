# Source Contract: DPWH Infrastructure Transparency Dataset

**Project:** DSS150P Zeta – DPWH Modular Data Pipeline
**Version:** 0.3
**Date:** 2026-10-07
**Companion documents:** `docs/data_dictionary.md` (what each column is), `docs/data_contract.md` (staged and curated column names), `docs/sources.md` (source register)
**Measured on:** `dpwh_transparency_data_all_details.parquet`, 248,421 rows, 52 columns
**Evidence:** `docs/evidence/07_raw_profiling.txt`, `docs/evidence/10_stage_first_run.txt` and the curated runs (`main`, tag `v0.3.0`)

This contract says what the pipeline expects from the source and what it does when the source breaks that expectation. It does not describe individual columns; see the data dictionary for that. Every rule and code below is the one the pipeline on `main` applies, unless it is marked **Planned**.

---

## 1. Parties and purpose

| Item | Detail |
| --- | --- |
| Data provider | BetterGov.ph, compiled from the DPWH Transparency Portal |
| Data consumer | Group Zeta pipeline (raw → staging → curated → PostgreSQL) |
| Purpose | Analysis of DPWH infrastructure contracts: budgets, progress, contractors, locations |
| Owner of this contract | Group Zeta |

## 2. Source and provenance

### Primary source

| Item | Detail |
| --- | --- |
| Dataset | `bettergovph/dpwh-transparency-data` on Hugging Face |
| File ingested | `dpwh_transparency_data_all_details.parquet` (the larger file) |
| File not ingested | `dpwh_transparency_data.parquet` (23 columns, the one the dataset card describes) |
| Revision | `648ea96af4f7625d606fda0b78803917913a26b7` (22 January 2026) |
| SHA-256 | `953f0bf99d162c062210219cc5f75c22df85049c24c0dae602c1f4dc976a7c97` (matches the published value) |
| Rows | 248,421, one per unique `contractId` |
| Columns | 52 |
| Licence | CC0 1.0 Universal |
| Collection method (by BetterGov, not by the team) | Third-party scraper using browser fingerprint impersonation, as stated in the release README |
| Coverage | About 93.5% of the 265,582 projects the portal reported on 30 September 2026 |
| Team's own collection | None. The team's API extractor received HTTP 403 on four runs on 30 September 2026 and stopped each time, as designed |
| Raw location | `data/raw/source=bettergov_hf/run_id=<UTC timestamp>/`, stored unchanged |
| Credit line | "Data credit: BetterGov.ph, compiled from the DPWH Transparency Portal." |

The earlier drafts were written from the dataset card, which describes the smaller file. Anything here that disagrees with the card is intentional.

### Reference source

| Item | Detail |
| --- | --- |
| Source | PSA Philippine Standard Geographic Code (PSGC), as of 30 June 2026 |
| File | `PSGC-2Q-2026-Publication-Datafile.xlsx` |
| Licence | CC BY 4.0 |
| SHA-256 | Recorded at download in `config/settings.yml` (the PSA does not publish checksums) |
| Raw lane | `source=psa_psgc` |
| Purpose | Official reference for checking `region`. It only flags; it never overwrites primary values |

## 3. Grain and keys

| Item | Rule |
| --- | --- |
| Grain | One row per contract |
| Primary key | `contractId`, unique in all 248,421 rows |
| Contractor members | `contractor` is split into one row per member in `contract_contractors.parquet`, keyed by `contract_id` and position |

---

## 4. Severities

| Severity | Meaning |
| --- | --- |
| **Stop** | The run ends and writes nothing |
| **Quarantine** | The row goes to `data/quarantine/` with its error code. The rest of the load continues |
| **Warn** | The row is kept and the code is added to its `warning_codes` |

Only structurally unusable rows are quarantined ("Traceable Rejections", spec Section 3). A row that is usable but suspicious is kept with a warning.

## 5. Run-level rules

| ID | Rule | Severity | Where |
| --- | --- | --- | --- |
| S1 | File SHA-256 matches `config/settings.yml` | Stop | `extract-file` |
| S2 | All 52 columns present, none missing and none unexpected | Stop | `stage` |
| S3 | Staged rows plus quarantined rows equal input rows | Stop | `stage`, `curate` |
| S4 | Staging files match the checksums in the staging report before curation reads them | Stop | `curate` |

The row count (248,421) is not checked separately: for this file version it is fixed by the hash in S1.

## 6. Row-level rules

| ID | Column(s) | Rule | Severity | Code | Observed (January 2026 release) |
| --- | --- | --- | --- | --- | --- |
| R1 | `contractId` | Not null or blank | Quarantine | `Q_MISSING_CONTRACT_ID` | 0 rows |
| R2 | `contractId` | Unique (every copy of a repeated ID) | Quarantine | `Q_DUPLICATE_CONTRACT_ID` | 0 rows |
| R3 | `abc`, `awardAmount` and other numeric columns | Parse as a number | Quarantine | `Q_BAD_NUMBER:<column>` | 0 rows |
| R4 | `infraYear`, `totalImages` | Whole number | Quarantine | `Q_BAD_INTEGER:<column>` | 0 rows |
| R5 | Date and date-time columns | Parse as ISO or `MM/DD/YYYY hh:mm:ss AM/PM` | Quarantine | `Q_BAD_DATE:<column>` | 0 rows |
| R6 | Date and date-time columns | Second format used | Warn | `W_DATE_FORMAT_ALT:<column>` | 9 rows (office `25FI`) |
| R7 | Date and date-time columns | `1900-01-01` placeholder becomes null | Warn | `W_PLACEHOLDER_DATE:<column>` | 446 `advertisementDate`, 186 `bidSubmissionDeadline` |
| R8 | `completionDate`, `startDate` | Completion not before start | Warn | `W_DATE_ORDER:completion_date` | 6 rows |
| R9 | `expiryDate`, `contractEffectivityDate` | Expiry not before effectivity | Warn | `W_DATE_ORDER:expiry_date` | 5 rows |
| R10 | `status` | Not null, and one of the 5 values in section 7 | Warn | `W_STATUS_MISSING`, `W_STATUS_UNKNOWN` | 0 rows |
| R11 | `progress` | Between 0 and 100 | Warn in staging, **Quarantine in curated** | `W_PROGRESS_RANGE`, then `Q_PROGRESS_OUT_OF_RANGE` | 3 rows (-100, -0.1, -0.02), quarantined in curated because the database accepts only 0 to 100 |
| R12 | Amount columns | Not negative | Warn in staging; for `budget` (`project_cost`), **Quarantine at load** | `W_NEGATIVE_AMOUNT:<column>`, then `Q_LOAD_NEGATIVE_PROJECT_COST` | 0 rows in the January 2026 release (evidence 22); the database accepts only `project_cost >= 0` |
| R13 | `latitude`, `longitude` | Both or neither present | Warn | `W_COORDINATES_PARTIAL` | |
| R14 | `latitude`, `longitude` | Inside latitude 4.0 to 21.5 and longitude 116.0 to 127.0 | Warn | `W_COORDINATES_OUTSIDE_PH` | 33,674 nulls; all present values inside |
| R15 | `verified` and other booleans | Recognised true or false value | Warn, value set to null | `W_BAD_BOOLEAN:<column>` | |
| R16 | `infraType_1`, `latitude_1`, `longitude_1` | Equal to the original column (the duplicate is dropped in staging) | Warn | `W_DUPLICATE_COLUMN_MISMATCH:<column>` | 0 rows |
| R17 | `contractor` | Present unless status is For Procurement | Warn | `W_CONTRACTOR_MISSING` | 8 rows (7 Completed, 1 Not Yet Started) |
| R18 | `contractor` | Every member has a trailing ID | Warn | `W_CONTRACTOR_ID_MISSING` | |
| R19 | `contractor` | Member name cut short by the source | Warn | `W_CONTRACTOR_NAME_TRUNCATED` | Names cut at 50 characters |
| R20 | `contractor` | Same member listed more than once (kept once) | Warn | `W_CONTRACTOR_DUPLICATE_MEMBER` | 34 contracts |
| R21 | `winnerNames`, `contractor` | Names agree, and both are present or both are absent | Warn | `W_WINNER_NAMES_MISMATCH`, `W_WINNER_NAMES_MISSING`, `W_WINNER_NAMES_WITHOUT_CONTRACTOR` | 40 mismatches after de-duplication |
| R22 | `region` | Matches one PSGC region | Warn | `W_REGION_NOT_IN_PSGC`, `W_REGION_AMBIGUOUS_IN_PSGC` | 297 unmatched (248,418 − 248,121), all "Central Office" (the DPWH head office, not a region) |
| R23 | Contractor members | Member's `contract_id` exists among the staged contracts | Quarantine (curated) | `Q_ORPHAN_CONTRACT_ID` | 0 rows |
| R24 | URL columns | Stored as text, **never fetched** | n/a | n/a | Links to `dcs.infrawatch.ph` |

`province` is not compared with the PSGC: it holds district engineering offices (for example "Quezon 2nd DEO"), not provinces.

### Planned, not enforced

| ID | Rule | Proposed severity | Note |
| --- | --- | --- | --- |
| P1 | `contractId` matches `^\d{2}[A-Z0-9]{2}\d{4}$` | Warn | Not yet checked on the full file |
| P2 | `amountPaid` is zero | Warn | Zero in every row; recorded as a quirk in section 9 |
| P3 | `Completed` rows have `progress = 100` | Warn | Documented quirk, see section 9 |
| P4 | Empty-value share per column does not change by more than 5 points between runs | Warn | Needs a second source revision to compare |

### Nullability changes from the earlier drafts

| Column | Earlier draft | Now |
| --- | --- | --- |
| `startDate` | Must not be null | Nullable (7,669 nulls) |
| `latitude`, `longitude` | Not seen empty | Nullable (33,674 nulls) |
| `contractor` | Yes (confirm) | Nullable (6,808 nulls, 6,800 of them For Procurement) |
| Livestream fields | Always empty | Mostly empty, 184 to 189 rows filled |

### Rules removed because the columns do not exist

- `reportCount >= 0`
- `hasSatelliteImage`
- Parsing the `location` JSON (`region` and `province` are plain columns)

---

## 7. Allowed values

### `status`

| Status | Rows |
| --- | --- |
| Completed | 205,109 |
| On-Going | 34,730 |
| For Procurement | 6,800 |
| Terminated | 929 |
| Not Yet Started | 853 |
| **Total** | **248,421** |

`status_1` holds undocumented codes and is not checked against an allowed list (see the data dictionary).

## 8. Format rules

- **Dates:** mostly ISO. Bid dates carry seven decimal places of seconds (`2021-10-27 00:00:00.0000000`). Nine rows from office `25FI` use `MM/DD/YYYY hh:mm:ss AM/PM`, read month first.
- **Placeholder date:** 1 January 1900 means "no date" and is stored as null.
- **`contractor`:** `NAME (id)`, `NAME ([REVOKED] id)`, or joint ventures as `A (id) / B (id)`. IDs are kept as text.
  - 11,625 joint-venture strings, 3,912 `[REVOKED]` markers.
  - Former names appear as `(FORMERLY: ...)`, `(PREVIOUSLY ...)` or `(FOR. ...)`.
  - The source cuts names at 50 characters (12,209 of 12,818 names with unbalanced brackets). Unbalanced brackets are not corruption.
- **`winnerNames`:** contractor names without IDs, sorted ignoring punctuation, joined with ", ". Empty string, not null, when there is no contractor.

## 9. Known quirks (documented, not "fixed")

- `budget` equals `abc` in 9.8% of rows and `awardAmount` in 50.0%; about 40% match neither. Do not use it as a single measure of cost.
- `amountPaid` is zero in every row.
- `infraType_1`, `latitude_1`, `longitude_1` repeat `infraType`, `latitude`, `longitude` in every row and are dropped in staging.
- `nysReason` is empty in every row.
- `On-Going` and `Terminated` rows can show `progress = 100`. These are kept.
- `infraYear` is published as text, holds whole numbers only, and can differ from the start year. It is kept as published.
- `completionDate` is empty for every On-Going contract, so the curated delay flag uses `expiryDate`.
- Fund codes such as `DA FMR`, `DepEd BEFF` and `SSP` are not defined in the source.

## 10. Freshness and versioning

| Item | Rule |
| --- | --- |
| Update frequency | None promised. The release is a fixed snapshot (revision `648ea96`) |
| Reruns | A second `extract-file` with the same file writes nothing and reports the existing run. Staging and curated rebuilds give the same `record_hash` for the same input |
| New revision | Treated as a new source version: record its SHA-256, re-profile, and update this contract's version and observed counts |

## 11. Change management

1. A change to a column name, type or allowed value list is a **breaking change**. The team agrees on it, then updates this contract, the data dictionary and `docs/data_contract.md` together.
2. Adding a column is **non-breaking**, but S2 stops the run until it is added to the expected schema.
3. Every change to this contract gets a version number and date in the change log.

## 12. Sign-off

| Name | Role | Date |
| --- | --- | --- |
| | | |

## Change log

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-10-06 | First draft from the dataset card, README and preview rows |
| 0.2 | 2026-10-06 | Rebuilt on the real 52-column file; rules, codes and severities aligned with the pipeline on `main`; unenforced rules moved to "Planned"; Appendix A removed (profiling is in `docs/evidence/07` and `10`) |
| 0.3 | 2026-10-07 | R12: negative `project_cost` quarantined at load (`Q_LOAD_NEGATIVE_PROJECT_COST`); null `project_cost` allowed in the database |
