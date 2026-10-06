```markdown
# Database Entity Relationship Diagram (ERD)
**Project:** Group Zeta DPWH Infrastructure Pipeline

This schema represents the final structured table loaded into the PostgreSQL data warehouse for downstream analytics.

```mermaid
erDiagram
    dpwh_curated_projects {
        string contract_id PK "Primary unique identifier for the project"
        float project_cost "Approved budget or award amount in PHP"
        float physical_accomplishment "Percentage of completion (0-100)"
        date start_date "Standardized ISO 8601 start date"
        int infra_year "Extracted year for partitioning"
        boolean is_delayed "Calculated flag based on target vs actual completion"
        string status_name "Current phase (e.g., Ongoing, Completed)"
        string record_hash "SHA-256 hash for idempotency and upsert validation"
    }