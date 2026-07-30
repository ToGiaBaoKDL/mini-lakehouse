"""Shared construction of thin EMR Serverless Airflow tasks."""

import shlex
from collections.abc import Mapping, Sequence
from datetime import timedelta

from airflow.providers.amazon.aws.operators.emr import EmrServerlessStartJobOperator

from orchestration.callbacks.notifications import task_failure_callbacks
from orchestration.config.templates import runtime_value

EMR_JOB_TIMEOUT_MINUTES = 120
AIRFLOW_TASK_TIMEOUT_MINUTES = 130


def emr_source_job(
    *,
    task_id: str,
    job_name: str,
    entry_point: str,
    entry_point_arguments: Sequence[str],
    spark_conf: Mapping[str, str],
) -> EmrServerlessStartJobOperator:
    code_uri = runtime_value("emr/code_uri")
    submit_parameters = shlex.join(
        [
            "--archives",
            f"{code_uri}/python.tar.gz#environment",
            "--conf",
            "spark.emr-serverless.driverEnv.PYSPARK_DRIVER_PYTHON=./environment/bin/python",
            "--conf",
            "spark.emr-serverless.driverEnv.PYSPARK_PYTHON=./environment/bin/python",
            "--conf",
            "spark.executorEnv.PYSPARK_PYTHON=./environment/bin/python",
            *(
                argument
                for key, value in spark_conf.items()
                for argument in ("--conf", f"{key}={value}")
            ),
        ]
    )
    return EmrServerlessStartJobOperator(
        task_id=task_id,
        name=job_name,
        application_id=runtime_value("emr/application_id"),
        execution_role_arn=runtime_value("emr/execution_role_arn"),
        job_driver={
            "sparkSubmit": {
                "entryPoint": f"{code_uri}/{entry_point}",
                "entryPointArguments": [
                    *entry_point_arguments,
                    "--landing-uri",
                    runtime_value("storage/landing_uri"),
                    "--contracts-uri",
                    f"{code_uri}/contracts.json",
                    "--catalog-name",
                    runtime_value("catalog/name"),
                ],
                "sparkSubmitParameters": submit_parameters,
            }
        },
        config={"executionTimeoutMinutes": EMR_JOB_TIMEOUT_MINUTES},
        aws_conn_id="aws_default",
        client_request_token=(
            "{{ dag.dag_id[:28] }}-"
            "{{ dag_run.run_after.strftime('%Y%m%dT%H%M%S%f') }}-"
            "{{ ti.try_number }}"
        ),
        deferrable=True,
        enable_application_ui_links=True,
        cancel_on_kill=True,
        retries=2,
        retry_delay=timedelta(minutes=5),
        execution_timeout=timedelta(minutes=AIRFLOW_TASK_TIMEOUT_MINUTES),
        waiter_delay=30,
        waiter_max_attempts=AIRFLOW_TASK_TIMEOUT_MINUTES * 2,
        on_failure_callback=task_failure_callbacks(),
    )
