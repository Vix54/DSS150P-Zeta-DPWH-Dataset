import logging
import traceback
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.param import Param
from airflow.operators.bash import BashOperator

PROJECT_DIR = "/opt/airflow"
PIPELINE = "/opt/pipeline-venv/bin/python -m src.cli"

log = logging.getLogger("dss150p_pipeline")


def failure_callback(context):
    task_instance = context.get("task_instance")
    dag_run = context.get("dag_run")
    exception = context.get("exception")
    trace = "".join(traceback.format_exception(exception)) if isinstance(exception, BaseException) else str(exception)
    log.error(
        "PIPELINE FAILURE dag=%s run_id=%s run_state=%s task=%s try=%s state=%s pipeline_run_id=airflow__%s\n%s",
        context.get("dag").dag_id if context.get("dag") else "unknown",
        dag_run.run_id if dag_run else "unknown",
        dag_run.get_state() if dag_run else "unknown",
        task_instance.task_id if task_instance else "unknown",
        task_instance.try_number if task_instance else "unknown",
        task_instance.state if task_instance else "unknown",
        context.get("ts_nodash"),
        trace,
    )


def pipeline_task(task_id, command, timeout_minutes):
    return BashOperator(
        task_id=task_id,
        bash_command=command,
        env={"PIPELINE_RUN_ID": "airflow__{{ ts_nodash }}"},
        append_env=True,
        cwd=PROJECT_DIR,
        execution_timeout=timedelta(minutes=timeout_minutes),
    )


default_args = {
    "owner": "group_zeta",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "on_failure_callback": failure_callback,
}

with DAG(
    dag_id="dss150p_pipeline",
    description="DPWH contracts: extract -> stage -> curate -> partition -> load -> validate -> benchmark, via the pipeline CLI only",
    default_args=default_args,
    schedule="0 2 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=3),
    tags=["dss150p", "group_zeta"],
    params={
        "run_mode": Param("full", enum=["full", "partition"], description="full loads every curated contract; partition loads one slice"),
        "target_year": Param(None, type=["null", "integer"], description="Start year of the slice (partition mode)"),
        "target_month": Param(None, type=["null", "integer"], minimum=1, maximum=12, description="Start month of the slice (optional)"),
        "controlled_failure": Param(False, type="boolean", description="Fail the validate task on its first try to demonstrate retry and recovery"),
    },
) as dag:
    slice_args = "--year {{ params.target_year }}{% if params.target_month %} --month {{ params.target_month }}{% endif %}"

    extract = pipeline_task(
        "extract",
        f"{PIPELINE} extract-file --source all",
        15,
    )
    stage = pipeline_task("stage", f"{PIPELINE} stage", 30)
    curate = pipeline_task("curate", f"{PIPELINE} curate", 30)
    partition = pipeline_task("partition", f"{PIPELINE} partition", 20)
    load = pipeline_task(
        "load",
        "{% if params.run_mode == 'partition' %}"
        f"{PIPELINE} load-partition {slice_args}"
        "{% else %}"
        f"{PIPELINE} load"
        "{% endif %}",
        30,
    )
    validate = pipeline_task(
        "validate",
        "{% if params.controlled_failure and ti.try_number == 1 %}"
        "echo 'Controlled failure: validate fails on try 1 so the retry and recovery can be observed' && exit 1"
        "{% else %}"
        "{% if params.run_mode == 'partition' %}"
        f"{PIPELINE} validate {slice_args}"
        "{% else %}"
        f"{PIPELINE} validate"
        "{% endif %}"
        "{% endif %}",
        30,
    )
    benchmark = pipeline_task(
        "benchmark",
        "{% if params.run_mode == 'full' %}"
        f"{PIPELINE} benchmark"
        "{% else %}"
        "echo 'Benchmark skipped in partition mode'"
        "{% endif %}",
        60,
    )

    extract >> stage >> curate >> partition >> load >> validate >> benchmark
