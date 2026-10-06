from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.models.param import Param
import logging

def failure_callback(context):
    ti = context.get('task_instance')
    logging.error(f"PIPELINE FAILURE: Task '{ti.task_id if ti else 'unknown'}' failed.")

default_args = {
    'owner': 'zeta',
    'depends_on_past': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=1),
    'execution_timeout': timedelta(minutes=15),
    'on_failure_callback': failure_callback,
}

with DAG(
    dag_id='dss150p_pipeline',
    default_args=default_args,
    schedule_interval='0 2 * * *',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    params={
        "run_mode": Param("full", enum=["full", "partition"]),
        "target_year": Param(2023, type=["integer", "null"]),
        "target_month": Param(None, type=["integer", "null"]),
        "controlled_failure": Param(False, type="boolean", description="Trigger a 1-time failure on validate")
    }
) as dag:
    
    env_vars = {'PIPELINE_RUN_ID': '{{ run_id }}'}

    extract = BashOperator(task_id='extract', bash_command='python -m src.cli extract-file', env=env_vars)
    stage = BashOperator(task_id='stage', bash_command='python -m src.cli stage', env=env_vars)
    curate = BashOperator(task_id='curate', bash_command='python -m src.cli curate', env=env_vars)
    load = BashOperator(task_id='load', bash_command='python -m src.cli load', env=env_vars)
    
    # Controlled Failure Toggle Logic
    val_cmd = """
    if [ "{{ params.controlled_failure }}" = "True" ] && [ "{{ task_instance.try_number }}" = "1" ]; then
        echo "Simulating controlled failure..."; exit 1;
    else
        python -m src.cli validate;
    fi
    """
    validate = BashOperator(task_id='validate', bash_command=val_cmd, env=env_vars)
    
    benchmark = BashOperator(task_id='benchmark', bash_command='python -m src.cli benchmark', env=env_vars)
    
    part_cmd = "python -m src.cli load-partition --year {{ params.target_year }} {% if params.target_month %}--month {{ params.target_month }}{% endif %}"
    partition = BashOperator(task_id='partition', bash_command=part_cmd, env=env_vars)

    extract >> stage >> curate >> load >> validate >> [benchmark, partition]