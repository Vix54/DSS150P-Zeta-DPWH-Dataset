import os
import sys
import logging
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from src.config import settings
from src.transform.curated import curated_contracts_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def run_partitioning(year, month):
    logger.info(f"Initiating partition pipeline for Year: {year}, Month: {month}")
    try:
        curated_path = curated_contracts_path(settings)
        if not Path(curated_path).exists():
            raise FileNotFoundError(f"Missing curated dataset at {curated_path}")

        df = pd.read_parquet(curated_path)
        if df.empty:
            logger.warning("Curated dataset is empty. Aborting partition logic.")
            return

        required_cols = ['start_date', 'infra_year']
        if not all(col in df.columns for col in required_cols):
            raise KeyError(f"Curated dataset is missing required partitioning columns: {required_cols}")
        
        # Safely extract month and handle malformed dates
        df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
        df['calc_month'] = df['start_date'].dt.month
        
        # Quarantine invalid years (missing or 9999)
        invalid_mask = df['infra_year'].isna() | (df['infra_year'] == 9999)
        quarantine_df = df[invalid_mask]
        
        # Isolate targeted partition
        valid_mask = (df['infra_year'] == year) & (df['calc_month'] == month) & (~invalid_mask)
        partition_df = df[valid_mask]
        
        if not quarantine_df.empty:
            quar_dir = Path("data/quarantine")
            quar_dir.mkdir(parents=True, exist_ok=True)
            quarantine_path = quar_dir / f"quarantine_{year}_{month}.parquet"
            quarantine_df.to_parquet(quarantine_path)
            logger.info(f"Quarantined {len(quarantine_df)} invalid records to {quarantine_path}")
            
        if partition_df.empty:
            logger.warning(f"No valid records match the target partition {year}-{month}.")
            records_inserted = 0
        else:
            part_dir = Path(f"data/partitioned/infra_year={year}/month={month}")
            part_dir.mkdir(parents=True, exist_ok=True)
            part_path = part_dir / "data.parquet"
            partition_df.to_parquet(part_path)
            records_inserted = len(partition_df)
            logger.info(f"Successfully wrote {records_inserted} rows to {part_path}")
        
        # Audit Logging with strict transaction management
        try:
            logger.info("Writing execution metrics to audit.partition_loads...")
            engine = create_engine(f"postgresql+psycopg2://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@{os.environ.get('POSTGRES_HOST')}:{os.environ.get('POSTGRES_PORT')}/{os.environ.get('POSTGRES_DB')}")
            
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO audit.partition_loads (load_year, load_month, records_inserted, records_updated)
                        VALUES (:yr, :mo, :ins, 0)
                    """),
                    {"yr": year, "mo": month, "ins": records_inserted}
                )
            logger.info("Audit log updated successfully.")
        except SQLAlchemyError as db_err:
            logger.error(f"Failed to write to audit schema. Database error: {db_err}")
            raise
            
    except Exception as e:
        logger.error(f"CRITICAL: Partition load failed. {str(e)}")
        raise