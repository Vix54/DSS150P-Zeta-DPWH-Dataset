# Data Flow & Lineage Diagram
**Project:** Group Zeta DPWH Infrastructure Pipeline

This diagram maps the transformation and movement of data from external extraction to the final curated analytical product.

```mermaid
flowchart TD
    %% Source to Raw
    subgraph External Sources
        A1[BetterGov API / HuggingFace]
        A2[PSA Geographic Data]
    end
    
    subgraph Raw Layer
        B1(data/raw/source=bettergov_hf/.../dpwh_transparency.parquet)
        B2(data/raw/source=psa_psgc/.../geographic_data.xlsx)
    end
    A1 -->|Python CLI Extractor| B1
    A2 -->|Python CLI Extractor| B2

    %% Raw to Staging
    subgraph Staging Layer
        C(data/staging/dpwh_staged.parquet)
    end
    B1 -->|Schema Validation & Null Check| C
    B2 -->|Region/Province Lookup| C

    %% Staging to Curated
    subgraph Curated Layer
        D(data/curated/dpwh_curated.parquet)
    end
    C -->|Business Rules, Text Standardization & Cost Variance| D

    %% Output
    subgraph Serving Layer
        E[(PostgreSQL: dpwh_curated_projects)]
        F[(data/partitioned/infra_year=YYYY/)]
    end
    D -->|Truncate & Load | E
    D -->|Hive Partitioning| F