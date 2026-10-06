import pandas as pd
import os
import logging
from sqlalchemy import create_engine, text

# Import the dynamic path resolver and settings per the team lead's instructions
from src.transform.curated import curated_contracts_path
from src.config import settings

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def load_to_postgres():
    """Loads curated Parquet data into PostgreSQL."""
    
    # Read dynamic path from settings
    curated_filepath = curated_contracts_path(settings)
    
    logging.info(f"Reading curated data from {curated_filepath}...")
    df = pd.read_parquet(curated_filepath)

    # Added status_name and record_hash to support hash-guarded upserts
    target_cols = [
        'contract_id', 'project_cost', 'physical_accomplishment', 
        'start_date', 'infra_year', 'is_delayed', 
        'status_name', 'record_hash'
    ]
    available_cols = [col for col in target_cols if col in df.columns]
    df_load = df[available_cols].copy()

    # Database connection forcing direct container environment variable reads
    db_user = os.environ.get("POSTGRES_USER", "postgres")
    db_pass = os.environ.get("POSTGRES_PASSWORD", "postgres")
    db_host = os.environ.get("POSTGRES_HOST", "postgres")
    db_port = os.environ.get("POSTGRES_PORT", "5432")
    db_name = os.environ.get("POSTGRES_DB", "postgres")

    engine = create_engine(f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

    logging.info("Executing TRUNCATE to ensure idempotency (rerun safety)...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dpwh_curated_projects RESTART IDENTITY CASCADE;"))
    
    logging.info("Loading new data into dpwh_curated_projects...")
    df_load.to_sql('dpwh_curated_projects', engine, if_exists='append', index=False, chunksize=5000)
    logging.info("Database load complete!")