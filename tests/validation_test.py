from src.transform.staging import stage_dpwh_data
from src.transform.curated import curate_dpwh_data

if __name__ == "__main__":
    raw_csv = "data/raw/dpwh_transparency_newest.csv"
    staging_pq = "data/staging/dpwh_staged.parquet"
    curated_pq = "data/curated/dpwh_curated.parquet"
    
    stage_dpwh_data(raw_csv, staging_pq)
    curate_dpwh_data(staging_pq, curated_pq)
    print("Local pipeline test complete!")