# Data Flow & Lineage Diagram
**Project:** Group Zeta DPWH Infrastructure Pipeline

# Data Lineage

This diagram maps the transformation and movement of data from external extraction to the final curated analytical product.

```mermaid
flowchart TD
    %% Source to Raw
    subgraph External Sources
        A1[File Download]
    end
    
    subgraph Raw Layer
        B1(data/raw/.../dpwh_transparency_data_all_details.parquet)
    end
    A1 --> B1

    %% Raw to Staging
    subgraph Staging Layer
        C(data/staging/dpwh_staged.parquet)
    end
    B1 -->|Schema Validation & Null Check| C

    %% Staging to Curated
    subgraph Curated Layer
        D(data/curated/dpwh_curated.parquet)
    end
    C -->|PSGC Region Check, Business Rules & Cost Variance| D

    %% Output
    subgraph Serving Layer
        E[(PostgreSQL: curated.dpwh_projects)]
        F[(data/partitioned/infra_year=YYYY/)]
        Q[(data/quarantine/)]
    end
    D -->|Hash-Guarded Upsert| E
    D -->|Hive Partitioning| F
    D -->|Quarantine Invalid Years| Q