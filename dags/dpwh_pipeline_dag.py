import os
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# Inject PIPELINE_RUN_ID into the environment for CLI tools
env_vars = os.environ.copy()
env_vars["PIPELINE_RUN_ID"] = "airflow__{{ ts_nodash }}"

default_args = {
    'owner': 'Group_Zeta',
    'depends_on_past': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=1),
}

with DAG(
    'dpwh_modular_pipeline',
    default_args=default_args,
    description='End-to-End DPWH Data Pipeline (Raw -> Staging -> Curated -> Postgres)',
    schedule_interval=None,
    start_date=datetime(2026, 1, 1),
    catchup=False
) as dag:

    # 1. Staging (Migrated to CLI)
    stage_raw_data_and_validate = BashOperator(
        task_id='stage_raw_data_and_validate',
        bash_command='python -m src.cli stage',
        env=env_vars
    )

    # 2. Curation (Migrated to CLI)
    apply_business_rules = BashOperator(
        task_id='apply_business_rules',
        bash_command='python -m src.cli curate',
        env=env_vars
    )

    # 3. Partition by Year
    def _execute_partitioning():
        # Using local imports so Airflow parses the DAG seamlessly
        from src.transform.curated import curated_contracts_path
        from src.config import settings
        
        # Updated to import from the transform folder
        from src.transform.partition import partition_by_year 
        
        partition_by_year(curated_contracts_path(settings))

    # 4. Load to PostgreSQL
    def _execute_postgres_load():
        from src.load.postgres import load_to_postgres
        load_to_postgres()

    load_to_postgres_task = PythonOperator(
        task_id='load_to_postgres',
        python_callable=_execute_postgres_load
    )

    # 5. Benchmarking
    # (Assuming benchmarking was also migrated to CLI. If not, convert to PythonOperator)
    benchmark_file_formats = BashOperator(
        task_id='benchmark_file_formats',
        bash_command='python -m src.cli benchmark',
        env=env_vars
    )

    # Define parallel branches as required
    stage_raw_data_and_validate >> apply_business_rules
    apply_business_rules >> [partition_by_year_task, load_to_postgres_task]
    [partition_by_year_task, load_to_postgres_task] >> benchmark_file_formats