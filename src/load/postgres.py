import os
import sys
import logging
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from src.transform.curated import curated_contracts_path
from src.config import settings

# Fulfills 4.15 Error Handling & Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_to_postgres():
    curated_filepath = curated_contracts_path(settings)
    df = pd.read_parquet(curated_filepath)
    
    # 1. Strict Credential Loading (No fallback defaults allowed)
    db_user = os.environ.get("POSTGRES_USER")
    db_pass = os.environ.get("POSTGRES_PASSWORD")
    db_host = os.environ.get("POSTGRES_HOST")
    db_port = os.environ.get("POSTGRES_PORT")
    db_name = os.environ.get("POSTGRES_DB")

    if not all([db_user, db_pass, db_host, db_port, db_name]):
        logging.error("CRITICAL: Missing database credentials in environment variables.")
        raise ValueError("Credentials must come from .env only. Fallbacks removed per team lead.")

    engine = create_engine(f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

    try:
        # 2. Upload to temporary staging table in the curated schema
        logging.info("Writing batch to temporary staging table...")
        df.to_sql('temp_load', engine, schema='curated', if_exists='replace', index=False)

        # 3. Hash-Guarded Upsert utilizing xmax for reporting counts
        upsert_query = text("""
            WITH upsert AS (
                INSERT INTO curated.dpwh_projects (
                    contract_id, project_cost, physical_accomplishment, start_date, 
                    infra_year, is_delayed, status_name, record_hash
                )
                SELECT 
                    contract_id, project_cost, physical_accomplishment, start_date, 
                    infra_year, is_delayed, status_name, record_hash
                FROM curated.temp_load
                ON CONFLICT (contract_id) DO UPDATE SET 
                    project_cost = EXCLUDED.project_cost,
                    physical_accomplishment = EXCLUDED.physical_accomplishment,
                    start_date = EXCLUDED.start_date,
                    infra_year = EXCLUDED.infra_year,
                    is_delayed = EXCLUDED.is_delayed,
                    status_name = EXCLUDED.status_name,
                    record_hash = EXCLUDED.record_hash
                WHERE curated.dpwh_projects.record_hash IS DISTINCT FROM EXCLUDED.record_hash
                RETURNING xmax
            )
            SELECT 
                COUNT(CASE WHEN xmax = 0 THEN 1 END) AS inserted,
                COUNT(CASE WHEN xmax::text::bigint > 0 THEN 1 END) AS updated
            FROM upsert;
        """)

        with engine.begin() as conn:
            result = conn.execute(upsert_query).fetchone()
            inserted = result[0] or 0
            updated = result[1] or 0
            
            # Cleanup temp table
            conn.execute(text("DROP TABLE curated.temp_load;"))
            
            logging.info(f"Database load complete. inserted={inserted}, updated={updated}")
            
    except SQLAlchemyError as e:
        logging.error(f"Database error during upsert: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise