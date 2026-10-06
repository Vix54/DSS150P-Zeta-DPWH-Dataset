import pandas as pd
import os
from src.validate.checks import run_quality_checks

def stage_dpwh_data(raw_filepath: str, staging_filepath: str):
    """Cleans raw DPWH data and writes it to the staging layer as Parquet."""
    print(f"Reading raw data from {raw_filepath}...")
    
    # Read the local CSV
    df = pd.read_csv(raw_filepath, low_memory=False)

    # Standardize column names (snake_case, lower, strip spaces)
    df.columns = df.columns.str.lower().str.replace(' ', '_').str.strip()

    # Standardize column names (snake_case, lower, strip spaces)
    df.columns = df.columns.str.lower().str.replace(' ', '_').str.strip()

    # Map the scraper's camelCase outputs to the pipeline's expected schema
    column_mapping = {
        'contractid': 'contract_id',
        'budget': 'project_cost',
        'progress': 'physical_accomplishment',
        'startdate': 'start_date',
        'infrayear': 'infra_year'
    }
    df.rename(columns=column_mapping, inplace=True)

    # Ensure required columns exist even if empty (prevents key errors)
    for col in ['project_cost', 'physical_accomplishment']:
        if col not in df.columns:
            df[col] = 0.0

    # Type Conversions & Missing Values
    df['project_cost'] = pd.to_numeric(df['project_cost'], errors='coerce').fillna(0.0)
    df['physical_accomplishment'] = pd.to_numeric(df['physical_accomplishment'], errors='coerce').fillna(0.0)
    
    # Date Standardization
    date_cols = [col for col in df.columns if 'date' in col]
    for col in date_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    # Run mandatory validation from checks.py
    run_quality_checks(df)

    # Save to Staging Layer as Parquet
    os.makedirs(os.path.dirname(staging_filepath), exist_ok=True)
    df.to_parquet(staging_filepath, index=False)
    print(f"Staged data saved successfully to {staging_filepath}")