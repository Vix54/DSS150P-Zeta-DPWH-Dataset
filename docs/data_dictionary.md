# Data Dictionary: DPWH Transparency Data

Target path: `docs/data_dictionary.md`
Companion document: `docs/source_contract.md` (rules and actions live there, not here)

Status: draft, updated 6 October 2026 using Enzo's facts sheet
Measured on: `dpwh_transparency_data_all_details.parquet`, 248,421 rows, 52 columns
Evidence: `docs/evidence/07_raw_profiling.txt` and `docs/evidence/10_stage_first_run.txt` (`main`)

This dictionary describes what each column is and what was observed in it. It does not say what the pipeline does when a value is bad. See the source contract for that.

Null counts are exact where measured. "7,283 or fewer" means the column was not among the 15 emptiest columns, so the exact count has not been taken yet.

---

## 1. Changes from the earlier drafts

The earlier drafts were built from the Hugging Face dataset card, which describes the smaller 23-column file.

| In the earlier drafts | In the real file |
| --- | --- |
| `location` (JSON with `province`, `region`) | Two plain text columns: `region` and `province` |
| `componentCategories` | Not present. Closest is `components` (list of records) |
| `reportCount` | Not present |
| `hasSatelliteImage` | Not present. Closest are `hasImages` (bool) and `totalImages` (int) |

33 of the 52 columns were not in the earlier drafts. They are marked **new**.

---

## 2. Columns

### Identity and description

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `contractId` | string | 0 | Unique in all 248,421 rows. Pattern not yet checked |
| `contractName` | string | 7,283 or fewer | **new** |
| `description` | string | 7,283 or fewer | |
| `category` | string | 7,283 or fewer | Full value list not yet taken |
| `infraType` | string | 7,283 or fewer | **new** |
| `infraType_1` | string | 7,283 or fewer | **new**. Identical to `infraType` in 100% of rows |
| `infraYear` | string | 7,283 or fewer | Every value is a whole number |
| `programName` | string | 7,283 or fewer | Full value list not yet taken |
| `sourceOfFunds` | string | 7,283 or fewer | |
| `fundingInstrument` | string | 7,283 or fewer | **new** |

### Status and progress

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `status` | string | 0 | 5 values, see section 3 |
| `status_1` | string | 1,830 | **new**. Undocumented codes, see section 4 |
| `progress` | float | 7,283 or fewer | Three negative values (-100, -0.1, -0.02). None above 100 |
| `nysReason` | null | 248,421 | **new**. Empty in every row |
| `isLive` | bool | 0 | |
| `isVerifiedByDpwh` | bool | 0 | **new** |
| `isVerifiedByPublic` | bool | 0 | **new** |
| `verified` | bool or null | 33,674 | **new** |

### Money

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `budget` | float | 7,283 or fewer | Equals `abc` in 9.8% of rows and `awardAmount` in 50.0%. About 40% match neither. Meaning is inconsistent |
| `abc` | numeric text | 7,283 or fewer | **new**. Approved budget for the contract. Every value parses as a number |
| `awardAmount` | numeric text | 7,283 or fewer | **new**. Every value parses as a number |
| `amountPaid` | int | 0 | Zero in every row |

### Location

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `region` | string | 7,283 or fewer | **new** (replaces `location.region`) |
| `province` | string | 7,283 or fewer | **new** (replaces `location.province`). Holds district engineering offices, e.g. "Quezon 2nd DEO" |
| `latitude` | float | 33,674 | All present values fall inside the Philippines box |
| `longitude` | float | 33,674 | Same |
| `latitude_1` | float | 33,674 | **new**. Identical to `latitude` in 100% of rows |
| `longitude_1` | float | 33,674 | **new**. Identical to `longitude` in 100% of rows |
| `coordinates` | list of records | 7,283 or fewer | **new**. Inner fields not yet profiled |

### Contractor and bidding

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `contractor` | string | 6,808 | 6,800 For Procurement, 7 Completed, 1 Not Yet Started. Format in section 5 |
| `winnerNames` | string | 0 | **new**. Empty string (not null) when there is no contractor |
| `bidders` | list of records | 7,283 or fewer | **new**. Inner fields not yet profiled |

### Dates

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `startDate` | date text | 7,669 | |
| `completionDate` | date text | 42,395 | Before `startDate` in 6 rows |
| `contractEffectivityDate` | date text | 7,283 | **new** |
| `expiryDate` | date text | 7,285 | **new**. Before `contractEffectivityDate` in 5 rows |
| `advertisementDate` | date-time text | 7,283 or fewer | **new**. 446 values are the placeholder 1900-01-01 |
| `bidSubmissionDeadline` | date-time text | 7,283 or fewer | **new**. 186 values are 1900-01-01 |
| `dateOfAward` | date-time text | 69,887 | **new** |
| `latestImageDate` | date-time text | 111,705 | **new** |

### Documents (URLs)

All link to `dcs.infrawatch.ph`. Store as text, never fetch.

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `advertisement` | URL text | 7,283 or fewer | **new** |
| `contractAgreement` | URL text | 7,283 or fewer | **new** |
| `noticeOfAward` | URL text | 7,283 or fewer | **new** |
| `noticeToProceed` | URL text | 7,283 or fewer | **new** |
| `programOfWork` | URL text | 7,283 or fewer | **new**. Empty strings in the rows inspected |
| `engineeringDesign` | URL text | 7,283 or fewer | **new**. Empty strings in the rows inspected |

### Components

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `components` | list of records | 7,283 or fewer | **new**. Inner fields not yet profiled. Closest to the old `componentCategories` |

### Images and livestream

| Column | Type | Nulls | Notes |
| --- | --- | --- | --- |
| `totalImages` | int | 0 | **new** |
| `hasImages` | bool | 0 | **new** |
| `livestreamUrl` | string | 248,237 | 184 rows filled |
| `livestreamVideoId` | string | 248,237 | 184 rows filled |
| `livestreamDetectedAt` | string | 248,232 | 189 rows filled |

Count check: 10 + 8 + 4 + 7 + 3 + 8 + 6 + 1 + 5 = 52 columns.

---

## 3. `status` values (complete list)

| Status | Rows |
| --- | --- |
| Completed | 205,109 |
| On-Going | 34,730 |
| For Procurement | 6,800 |
| Terminated | 929 |
| Not Yet Started | 853 |
| **Total** | **248,421** |

## 4. `status_1` codes (undocumented)

The source does not explain these codes. Do not guess their meaning in downstream use.

| Code | Rows |
| --- | --- |
| F | 138,363 |
| A | 103,747 |
| CP | 3,747 |
| T | 498 |
| ANV | 186 |
| FNV | 45 |
| TNV | 3 |
| C | 2 |
| null | 1,830 |
| **Total** | **248,421** |

---

## 5. Value formats

- **Dates:** mostly ISO. Bid dates carry seven decimal places of seconds (`2021-10-27 00:00:00.0000000`). Nine rows from office `25FI` use `MM/DD/YYYY hh:mm:ss AM/PM`.
- **Placeholder date:** 1 January 1900 means "no date".
- **`contractor`:** `NAME (id)`, `NAME ([REVOKED] id)`, or joint ventures as `A (id) / B (id)`.
  - 11,625 joint-venture strings and 3,912 `[REVOKED]` markers.
  - 34 contracts list the same contractor more than once.
  - Former names appear as `(FORMERLY: ...)`, `(PREVIOUSLY ...)` or `(FOR. ...)`.
  - The source cuts names at 50 characters (12,209 of 12,818 names with unbalanced brackets). The longest name is 100 characters.
- **`winnerNames`:** contractor names without IDs, sorted ignoring punctuation, joined with ", ". Forty contracts still disagree after removing repeats.

---

## 6. Redundant or empty columns

| Column | Observation |
| --- | --- |
| `infraType_1` | Same as `infraType` in every row |
| `latitude_1`, `longitude_1` | Same as `latitude`, `longitude` in every row |
| `nysReason` | Empty in every row |
| `amountPaid` | Zero in every row |
| `programOfWork`, `engineeringDesign` | Empty strings in the rows inspected |

---

## 7. Not yet profiled

- Full value lists for `programName` and `category`
- Exact null counts for columns marked "7,283 or fewer"
- Inner fields of `components`, `bidders` and `coordinates`
- Whether any contractor ID starts with a zero
- Whether every `contractId` matches `^\d{2}[A-Z0-9]{2}\d{4}$`
