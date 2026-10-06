from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
import logging

def on_failure_callback(context):
    logging.error(f"PIPELINE FAILURE: Task {context['task_instance_key_str']} failed.")

default_args = {
    'owner': 'zeta',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=2),
    'execution_timeout': timedelta(minutes=15),
    'on_failure_callback': on_failure_callback,
}

with DAG(
    dag_id='dss150p_pipeline',
    default_args=default_args,
    description='DPWH Infrastructure Data Pipeline',
    schedule_interval='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['dpwh', 'zeta'],
) as dag:

    # Fulfills requirement: Every task calls the CLI via BashOperator
    extract_task = BashOperator(
        task_id='extract',
        bash_command='python -m src.cli extract'
    )

    transform_task = BashOperator(
        task_id='transform',
        bash_command='python -m src.cli stage && python -m src.cli curate'
    )

    load_task = BashOperator(
        task_id='load',
        bash_command='python -m src.cli load'
    )

    validate_task = BashOperator(
        task_id='validate',
        bash_command='python -m src.cli validate'
    )

    # Fulfills requirement: extract >> transform >> load >> validate
    extract_task >> transform_task >> load_task >> validate_task