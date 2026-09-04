"""Shared construction of isolated local Docker tasks."""

import os
from collections.abc import Mapping, Sequence
from datetime import timedelta

from airflow.providers.docker.operators.docker import DockerOperator
from airflow.sdk import Asset, Context
from callbacks.notifications import task_failure_callbacks
from docker.types import Mount


class LoggedDockerOperator(DockerOperator):
    """Add an explicit terminal message after the container exits successfully."""

    def execute(self, context: Context) -> list[str] | str | None:
        result = super().execute(context)
        container_id = self.container["Id"][:12] if self.container else "unknown"
        self.log.info(
            "Docker workload completed successfully: image=%s container=%s",
            self.image,
            container_id,
        )
        return result


def docker_task(
    *,
    task_id: str,
    image: str,
    command: Sequence[str],
    workload: str,
    execution_timeout: timedelta,
    environment: Mapping[str, str] | None = None,
    cpus: float = 1.0,
    mem_limit: str | None = None,
    retries: int = 0,
    retry_delay: timedelta = timedelta(minutes=5),
    do_xcom_push: bool = False,
    inlets: Sequence[Asset] = (),
    outlets: Sequence[Asset] = (),
    skip_on_exit_code: int | None = None,
) -> LoggedDockerOperator:
    task_environment = {
        **(environment or {}),
        "AWS_CONFIG_FILE": "/run/aws/config",
        "AWS_EC2_METADATA_DISABLED": "true",
        "LAKEHOUSE_ENVIRONMENT": os.environ["LAKEHOUSE_ENVIRONMENT"],
    }
    return LoggedDockerOperator(
        task_id=task_id,
        image=image,
        command=list(command),
        docker_url="unix://var/run/docker.sock",
        environment=task_environment,
        cpus=cpus,
        mem_limit=mem_limit,
        mounts=[
            Mount(
                source=f"{os.environ['HOST_AWS_IDENTITY_DIR']}/{workload}",
                target="/run/aws",
                type="bind",
                read_only=True,
            )
        ],
        user=f"{os.getuid()}:0",
        force_pull=False,
        auto_remove="force",
        mount_tmp_dir=False,
        execution_timeout=execution_timeout,
        retries=retries,
        retry_delay=retry_delay,
        do_xcom_push=do_xcom_push,
        inlets=list(inlets),
        outlets=list(outlets),
        on_failure_callback=task_failure_callbacks(),
        skip_on_exit_code=skip_on_exit_code,
    )
