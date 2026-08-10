"""Shared construction of thin EMR Serverless Airflow tasks."""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from airflow.providers.amazon.aws.operators.emr import EmrServerlessStartJobOperator
from airflow.sdk import Asset, Context

from airflow_bundle.callbacks.notifications import task_failure_callbacks
from airflow_bundle.config.templates import runtime_value

EMR_JOB_TIMEOUT_MINUTES = 120
AIRFLOW_TASK_TIMEOUT_MINUTES = 130


class LoggedEmrServerlessStartJobOperator(EmrServerlessStartJobOperator):
    """Add an explicit terminal message after the provider waiter succeeds."""

    def execute(self, context: Context, event: dict[str, Any] | None = None) -> str | None:
        job_id = super().execute(context, event)
        self.log.info("EMR Serverless job completed successfully: %s", job_id)
        return job_id


def emr_spark_job(
    *,
    task_id: str,
    job_name: str,
    entry_point: str,
    entry_point_arguments: Sequence[str],
    spark_conf: Mapping[str, str],
    outlets: Sequence[Asset] = (),
) -> LoggedEmrServerlessStartJobOperator:
    code_uri = runtime_value("emr/code_uri")
    effective_spark_conf = {
        "spark.executor.instances": "1",
        "spark.dynamicAllocation.minExecutors": "0",
        "spark.dynamicAllocation.initialExecutors": "1",
        **spark_conf,
    }
    submit_parameters = " ".join(
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
                for key, value in effective_spark_conf.items()
                for argument in ("--conf", f"{key}={value}")
            ),
        ]
    )
    return LoggedEmrServerlessStartJobOperator(
        task_id=task_id,
        name=job_name,
        application_id=runtime_value("emr/application_id"),
        execution_role_arn=runtime_value("emr/execution_role_arn"),
        job_driver={
            "sparkSubmit": {
                "entryPoint": f"{code_uri}/{entry_point}",
                "entryPointArguments": [
                    *entry_point_arguments,
                    "--contracts-uri",
                    f"{code_uri}/contracts.json",
                ],
                "sparkSubmitParameters": submit_parameters,
            }
        },
        config={"executionTimeoutMinutes": EMR_JOB_TIMEOUT_MINUTES},
        aws_conn_id=None,
        wait_for_completion=True,
        deferrable=False,
        enable_application_ui_links=True,
        cancel_on_kill=True,
        retries=1,
        retry_delay=timedelta(minutes=10),
        execution_timeout=timedelta(minutes=AIRFLOW_TASK_TIMEOUT_MINUTES),
        waiter_delay=30,
        waiter_max_attempts=AIRFLOW_TASK_TIMEOUT_MINUTES * 2,
        outlets=list(outlets),
        on_failure_callback=task_failure_callbacks(),
    )
