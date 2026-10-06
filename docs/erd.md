```markdown
# Database Entity Relationship Diagram (ERD)
**Project:** Group Zeta DPWH Infrastructure Pipeline

This schema represents the final structured table loaded into the PostgreSQL data warehouse for downstream analytics.

```mermaid
erDiagram
    dpwh_projects {
        string contract_id PK "Primary identifier"
        numeric project_cost "Approved budget (>= 0)"
        numeric physical_accomplishment "Percentage of completion (0-100)"
        date start_date "Standardized start date"
        int infra_year "Extracted year for partitioning"
        boolean is_delayed "True if project execution exceeds target timeframe"
        string status_name "Current phase"
        string record_hash "SHA-256 hash for upsert validation"
    }