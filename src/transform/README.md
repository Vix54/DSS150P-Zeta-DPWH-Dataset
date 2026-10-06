# Data Transformation Rules and Assumptions

This directory contains the modular Python processing logic for the DPWH Infrastructure Data Pipeline. The transformation is divided into two distinct layers to preserve traceability and separate data cleaning from business logic.

## 1. Staging Layer (`staging.py`)
The staging layer ingests raw source data (CSV/Parquet) and applies structural standardization, type safety, and missing-value treatments.

**Applied Transformations:**
*   **Column Standardization:** All column headers are converted to lowercase, stripped of whitespace, and mapped from camelCase to snake_case (e.g., `contractId` to `contract_id`, `budget` to `project_cost`, `progress` to `physical_accomplishment`) to enforce a unified database schema.
*   **Type Conversion:** Financial and progress metrics (`project_cost`, `physical_accomplishment`) are strictly cast to floats using `pd.to_numeric`. Date fields are cast to valid datetime objects.
*   **Missing-Value Treatment:** Null values in numeric columns are defensively imputed with `0.0` using `.fillna(0.0)` to prevent runtime errors during downstream aggregations.

## 2. Automated Data Quality Validation (`../validation/checks.py`)
Before data is permitted into the curated layer, the pipeline enforces five strict data quality checks.

**Validation Rules:**
1.  **Schema Enforcement:** The pipeline verifies the presence of essential columns (`contract_id`, `project_cost`, `physical_accomplishment`).
2.  **Nullability Check:** The primary key (`contract_id`) cannot contain nulls.
3.  **Uniqueness (Duplicate Handling):** Duplicate `contract_id` records are logged as warnings and actively dropped to preserve referential integrity.
4.  **Data Type Validation:** Verifies that the `project_cost` field is strictly numeric.
5.  **Domain/Range Rules:** Enforces that `physical_accomplishment` falls between 0 and 100. Out-of-bounds anomalies are capped to their nearest logical limit (0 or 100) and logged.

## 3. Curated Layer (`curated.py`)
The curated layer consumes the validated staging data and applies domain-specific business rules to prepare the dataset for final analytics and PostgreSQL loading.

**Applied Transformations:**
*   **Filtering:** Rows where the project `status` is explicitly marked as 'cancelled' are filtered out of the final dataset.
*   **Derived-Field Creation:** A new boolean column, `is_delayed`, is generated. This rule flags any project where the `target_completion_date` is strictly less than the current execution date and the `physical_accomplishment` is less than 100%.

**Assumptions:**
*   Projects missing target completion dates cannot be accurately flagged for delays and will default to `False`.
*   A physical accomplishment of exactly 100% denotes a fully completed project, regardless of the official status string.