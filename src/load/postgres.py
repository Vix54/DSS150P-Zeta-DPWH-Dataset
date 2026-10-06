import os
import sys
import logging
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from src.config import load_settings
from src.transform.curated import curated_contracts_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def load_to_postgres():
    logger.info("Initializing database load...")
    try:
        # Fulfills remark to dynamically load settings inside the function
        settings = load_settings()
        curated_filepath = curated_contracts_path(settings)
        
        df = pd.read_parquet(curated_filepath)
        initial_count = len(df)
        
        # Pre-load validation: Prune null or negative costs to protect the DB transaction
        valid_df = df.dropna(subset=['project_cost'])
        valid_df = valid_df[valid_df['project_cost'] >= 0]
        
        dropped_count = initial_count - len(valid_df)
        if dropped_count > 0:
            logger.warning(f"Quarantined {dropped_count} rows with null or negative project_cost before DB load.")

        db_user = os.environ.get("POSTGRES_USER")
        db_pass = os.environ.get("POSTGRES_PASSWORD")
        db_host = os.environ.get("POSTGRES_HOST")
        db_port = os.environ.get("POSTGRES_PORT")
        db_name = os.environ.get("POSTGRES_DB")

        if not all([db_user, db_pass, db_host, db_port, db_name]):
            raise ValueError("Missing required database credentials in environment variables.")

        engine = create_engine(f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

        logger.info(f"Writing {len(valid_df)} valid records to staging table...")
        valid_df.to_sql('temp_load', engine, schema='curated', if_exists='replace', index=False)

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
            conn.execute(text("DROP TABLE curated.temp_load;"))
            
            logger.info(f"Database load complete. inserted={inserted}, updated={updated}")
            
    except SQLAlchemyError as db_err:
        logger.error(f"DATABASE ERROR: {db_err}")
        raise
    except Exception as e:
        logger.error(f"CRITICAL ERROR: {str(e)}")
        raise