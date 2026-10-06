import os
import sys
import time
import statistics
import logging
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from src.config import load_settings
from src.transform.curated import curated_contracts_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def run_benchmarks():
    logger.info("Initializing benchmarking suite...")
    try:
        settings = load_settings()
        curated_path = curated_contracts_path(settings)
        if not Path(curated_path).exists():
            raise FileNotFoundError(f"Curated file not found at {curated_path}")

        df = pd.read_parquet(curated_path)
        bench_dir = Path("data/benchmarks")
        bench_dir.mkdir(parents=True, exist_ok=True)

        results = []
        runs = 5

        def measure(func, *args, **kwargs):
            times = []
            for _ in range(runs):
                start = time.perf_counter()
                res = func(*args, **kwargs)
                times.append(time.perf_counter() - start)
            return statistics.median(times), res

        # 1. CSV
        try:
            logger.info("Benchmarking CSV format...")
            csv_path = bench_dir / "bench.csv"
            write_csv, _ = measure(df.to_csv, csv_path, index=False)
            read_csv, _ = measure(pd.read_csv, csv_path)
            filt_csv, _ = measure(lambda: pd.read_csv(csv_path)[lambda x: x['status_name'] == 'On-Going'])
            results.append({'Format': 'CSV', 'Size_MB': os.path.getsize(csv_path)/1e6, 'Write_s': write_csv, 'Read_s': read_csv, 'Filter_s': filt_csv})
        except Exception as e:
            logger.error(f"CSV benchmarking failed: {e}")

        # 2. JSON Lines
        try:
            logger.info("Benchmarking JSON Lines format...")
            json_path = bench_dir / "bench.jsonl"
            # Convert dates to string safely for JSON serialization
            json_df = df.copy()
            if 'start_date' in json_df:
                json_df['start_date'] = json_df['start_date'].astype(str)

            write_json, _ = measure(json_df.to_json, json_path, orient='records', lines=True)
            read_json, _ = measure(pd.read_json, json_path, orient='records', lines=True)
            filt_json, _ = measure(lambda: pd.read_json(json_path, orient='records', lines=True)[lambda x: x['status_name'] == 'On-Going'])
            results.append({'Format': 'JSONL', 'Size_MB': os.path.getsize(json_path)/1e6, 'Write_s': write_json, 'Read_s': read_json, 'Filter_s': filt_json})
        except Exception as e:
            logger.error(f"JSONL benchmarking failed: {e}")

        # 3. Parquet (Snappy) & 4. Parquet (Zstd)
        formats = [('Snappy', 'snappy'), ('Zstd', 'zstd')]
        for name, comp in formats:
            try:
                logger.info(f"Benchmarking Parquet ({name})...")
                p_path = bench_dir / f"bench_{comp}.parquet"
                write_p, _ = measure(df.to_parquet, p_path, compression=comp)
                read_p, _ = measure(pd.read_parquet, p_path)
                filt_p, _ = measure(lambda: pd.read_parquet(p_path, filters=[('status_name', '==', 'On-Going')]))
                results.append({'Format': f'Parquet ({name})', 'Size_MB': os.path.getsize(p_path)/1e6, 'Write_s': write_p, 'Read_s': read_p, 'Filter_s': filt_p})
            except Exception as e:
                logger.error(f"Parquet ({name}) benchmarking failed: {e}")

        # 5. PostgreSQL
        engine = None
        try:
            logger.info("Benchmarking PostgreSQL...")
            engine = create_engine(
                f"postgresql+psycopg2://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@"
                f"{os.environ.get('POSTGRES_HOST')}:{os.environ.get('POSTGRES_PORT')}/{os.environ.get('POSTGRES_DB')}"
            )

            write_pg, _ = measure(df.to_sql, 'bench_temp', engine, schema='curated', if_exists='replace', index=False)
            read_pg, _ = measure(lambda: pd.read_sql(text('SELECT * FROM curated.bench_temp'), engine))
            filt_pg, _ = measure(lambda: pd.read_sql(text("SELECT * FROM curated.bench_temp WHERE status_name = 'On-Going'"), engine))
            
            with engine.begin() as conn:
                pg_size = conn.execute(text("SELECT pg_total_relation_size('curated.bench_temp')")).scalar()
            
            results.append({
                'Format': 'PostgreSQL',
                'Size_MB': pg_size / 1e6 if pg_size else 0,
                'Write_s': write_pg,
                'Read_s': read_pg,
                'Filter_s': filt_pg,
            })
        except Exception as e:
            logger.error(f"PostgreSQL benchmarking failed: {e}")
        finally:
            if engine is not None:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("DROP TABLE IF EXISTS curated.bench_temp CASCADE"))
                    logger.info("Dropped benchmark temp table.")
                except Exception as cleanup_err:
                    logger.warning(f"Failed to drop PostgreSQL benchmark temp table: {cleanup_err}")

        logger.info("Benchmarking suite complete.")
        res_df = pd.DataFrame(results)
        
        # Save to disk as required by Phia's instructions
        out_file = bench_dir / "benchmark_results.csv"
        res_df.to_csv(out_file, index=False)
        logger.info(f"Successfully saved benchmark report to {out_file}")
        
        return res_df

    except Exception as e:
        logger.exception("Benchmarking suite failed: %s", e)
        raise