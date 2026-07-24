import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.exceptions import ObjectNotFound
from prefect.workers.process import ProcessWorker
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concurrency_limit: int = Field(ge=1)


class WorkPoolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    type: Literal["process"]
    concurrency_limit: int = Field(ge=1)


class WorkQueuePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    priority: int = Field(ge=1)
    concurrency_limit: int = Field(ge=1)


class PrefectRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    worker: WorkerPolicy
    work_pool: WorkPoolPolicy
    work_queues: tuple[WorkQueuePolicy, ...]

    @model_validator(mode="after")
    def validate_policy(self) -> "PrefectRuntimePolicy":
        names = [queue.name for queue in self.work_queues]
        priorities = [queue.priority for queue in self.work_queues]
        if len(names) != len(set(names)):
            raise ValueError("Prefect work queue names must be unique")
        if len(priorities) != len(set(priorities)):
            raise ValueError("Prefect work queue priorities must be unique")
        if self.worker.concurrency_limit != self.work_pool.concurrency_limit:
            raise ValueError("Local worker and work pool concurrency limits must match")
        return self


def load_runtime_policy(path: Path = Path("prefect.yaml")) -> PrefectRuntimePolicy:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        runtime = document["definitions"]["runtime"]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Prefect runtime policy is missing from {path}") from error
    return PrefectRuntimePolicy.model_validate(runtime)


async def reconcile_runtime(policy: PrefectRuntimePolicy) -> None:
    async with get_client() as client:
        await client.create_work_pool(
            WorkPoolCreate(
                name=policy.work_pool.name,
                type=policy.work_pool.type,
                base_job_template=ProcessWorker.get_default_base_job_template(),
                is_paused=False,
                concurrency_limit=policy.work_pool.concurrency_limit,
            ),
            overwrite=True,
        )

        for queue in policy.work_queues:
            try:
                current = await client.read_work_queue_by_name(
                    queue.name,
                    work_pool_name=policy.work_pool.name,
                )
            except ObjectNotFound:
                await client.create_work_queue(
                    name=queue.name,
                    work_pool_name=policy.work_pool.name,
                    is_paused=False,
                    concurrency_limit=queue.concurrency_limit,
                    priority=queue.priority,
                )
                continue

            await client.update_work_queue(
                current.id,
                is_paused=False,
                concurrency_limit=queue.concurrency_limit,
                priority=queue.priority,
            )


def start_worker(policy: PrefectRuntimePolicy) -> None:
    os.execvp(
        "prefect",
        (
            "prefect",
            "worker",
            "start",
            "--pool",
            policy.work_pool.name,
            "--type",
            policy.work_pool.type,
            "--limit",
            str(policy.worker.concurrency_limit),
            "--no-create-pool-if-not-found",
            "--with-healthcheck",
        ),
    )


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reconcile or run the local Prefect runtime.")
    parser.add_argument("action", choices=("reconcile", "worker"))
    parsed = parser.parse_args(arguments)
    policy = load_runtime_policy()
    if parsed.action == "reconcile":
        asyncio.run(reconcile_runtime(policy))
        return
    start_worker(policy)


if __name__ == "__main__":
    main()
