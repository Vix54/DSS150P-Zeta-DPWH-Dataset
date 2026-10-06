import pandas as pd
import os
import logging
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def load_to_postgres(curated_filepath: str):
    """Loads curated Parquet data into PostgreSQL using a controlled truncate-and-load strategy."""
    logging.info(f"Reading curated data from {curated_filepath}...")
    df = pd.read_parquet(curated_filepath)

    # Filter only columns that belong in the database schema
    target_cols = ['contract_id', 'project_cost', 'physical_accomplishment', 'start_date', 'infra_year', 'is_delayed']
    available_cols = [col for col in target_cols if col in df.columns]
    df_load = df[available_cols].copy()

    # Database connection using environment variables
    db_user = os.getenv("POSTGRES_USER", "postgres")
    db_pass = os.getenv("POSTGRES_PASSWORD", "postgres")
    db_host = os.getenv("POSTGRES_HOST", "localhost")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "postgres")

    engine = create_engine(f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

    logging.info("Executing TRUNCATE to ensure idempotency (rerun safety)...")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE dpwh_curated_projects RESTART IDENTITY CASCADE;"))
    
    logging.info("Loading new data into dpwh_curated_projects...")
    df_load.to_sql('dpwh_curated_projects', engine, if_exists='append', index=False, chunksize=5000)
    logging.info("Database load complete!")