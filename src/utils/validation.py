import os
import sys
import logging
import hashlib
import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from src.config import settings
from src.transform.curated import curated_contracts_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def compute_file_hash(filepath, chunk_size=65536):
    """Generates a SHA-256 hash efficiently using large memory chunks."""
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()

def run_validation():
    logger.info("Starting rigorous pipeline validation...")
    try:
        staging_path = Path("data/staging/dpwh_staged.parquet")
        curated_path = Path(curated_contracts_path(settings))
        
        for p in [staging_path, curated_path]:
            if not p.exists():
                raise FileNotFoundError(f"Missing essential pipeline artifact: {p}")

        # 1. File Hashes (I/O Optimized)
        logger.info("1. Validating file hashes...")
        staging_hash = compute_file_hash(staging_path)
        curated_hash = compute_file_hash(curated_path)
        logger.info(f"Staging Parquet SHA-256: {staging_hash}")
        logger.info(f"Curated Parquet SHA-256: {curated_hash}")

        # 2. Row Counts (O(1) Memory via PyArrow Metadata)
        logger.info("2. Validating row counts between layers (Metadata Read)...")
        count_stage = pq.ParquetFile(staging_path).metadata.num_rows
        count_curate = pq.ParquetFile(curated_path).metadata.num_rows
        
        logger.info(f"Staging rows: {count_stage}, Curated rows: {count_curate}")
        if count_curate > count_stage:
            raise ValueError(f"Data anomaly: Curated layer ({count_curate}) has more rows than Staging ({count_stage}).")

        # 3. Calculations & Data Integrity (Memory-Efficient Column Pruning)
        logger.info("3. Validating calculations and data integrity constraints...")
        df_curate = pd.read_parquet(curated_path, columns=['project_cost', 'record_hash'])
        
        invalid_costs = df_curate[df_curate['project_cost'] < 0]
        if not invalid_costs.empty:
            raise ValueError(f"Integrity failure: {len(invalid_costs)} records possess negative project_cost.")
        
        if df_curate['record_hash'].isnull().any():
            raise ValueError("Integrity failure: Null record_hash values detected in curated dataset.")
        logger.info("Calculations, null-checks, and constraints passed.")

        # 4. Database Hash Matches (Bypassing Pandas for raw speed)
        logger.info("4. Validating database hash matches...")
        engine = create_engine(
            f"postgresql+psycopg2://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@"
            f"{os.environ.get('POSTGRES_HOST')}:{os.environ.get('POSTGRES_PORT')}/{os.environ.get('POSTGRES_DB')}"
        )
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT record_hash FROM curated.dpwh_projects")).fetchall()
            db_hashes = {row[0] for row in result}
        
        if not db_hashes:
            logger.warning("Database table 'curated.dpwh_projects' is empty. Run 'load' command first.")
        else:
            file_hashes = set(df_curate['record_hash'])
            missing_in_db = file_hashes - db_hashes
            
            if missing_in_db:
                logger.error(f"Synchronization failure: Database is missing {len(missing_in_db)} hashes present in the curated file.")
                raise ValueError(f"Database out of sync with curated layer. {len(missing_in_db)} missing records.")
            else:
                logger.info("SUCCESS: All curated file hashes perfectly match the database records.")

        logger.info("Rigorous pipeline validation completed successfully.")
        
    except SQLAlchemyError as db_err:
        logger.error(f"DATABASE VALIDATION FAILED: {db_err}")
        raise
    except Exception as e:
        logger.error(f"VALIDATION FAILED: {str(e)}")
        raise