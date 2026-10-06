import pandas as pd
import os

def partition_curated_data(curated_filepath: str, partitioned_dir: str):
    """Partitions the curated dataset by infra_year for optimized querying."""
    print(f"Partitioning data from {curated_filepath}...")
    df = pd.read_parquet(curated_filepath)
    
    if 'infra_year' in df.columns:
        # Fill missing years with a placeholder so partitioning doesn't fail
        df['infra_year'] = df['infra_year'].fillna(9999).astype(int)
        
        # Write dataset partitioned by year using pyarrow
        os.makedirs(partitioned_dir, exist_ok=True)
        df.to_parquet(partitioned_dir, partition_cols=['infra_year'], engine='pyarrow', index=False)
        print(f"Data successfully partitioned by infra_year in {partitioned_dir}/")
    else:
        print("Error: infra_year column missing. Cannot partition.")