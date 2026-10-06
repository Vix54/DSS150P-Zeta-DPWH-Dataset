import pandas as pd
import os

def curate_dpwh_data(staging_filepath: str, curated_filepath: str):
    """Applies business rules to create the final analytical dataset."""
    print(f"Reading staged data from {staging_filepath}...")
    df = pd.read_parquet(staging_filepath)

    # Business Rule 1: Flag projects that are severely delayed
    if 'target_completion_date' in df.columns:
        current_date = pd.Timestamp.now()
        df['is_delayed'] = (df['target_completion_date'] < current_date) & (df['physical_accomplishment'] < 100)
    
    # Business Rule 2: Exclude cancelled projects
    if 'status' in df.columns:
        df = df[df['status'].astype(str).str.lower() != 'cancelled']

    # Save to Curated Layer as Parquet
    os.makedirs(os.path.dirname(curated_filepath), exist_ok=True)
    df.to_parquet(curated_filepath, index=False)
    print(f"Curated dataset saved successfully to {curated_filepath}")