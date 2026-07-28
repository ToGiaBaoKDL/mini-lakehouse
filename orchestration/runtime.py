import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

import yaml
from prefect.client.orchestration import PrefectClient, get_client
from prefect.client.schemas.actions import WorkPoolCreate, WorkQueueCreate
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound
from prefect.workers.process import ProcessWorker
from pydantic import TypeAdapter

type PrefectRuntime = tuple[WorkPoolCreate, tuple[WorkQueueCreate, ...]]

_WORK_QUEUES = TypeAdapter(tuple[WorkQueueCreate, ...])


def load_runtime(path: Path = Path("prefect.yaml")) -> PrefectRuntime:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        runtime = document["definitions"]["runtime"]
        work_pool = WorkPoolCreate.model_validate(runtime["work_pool"])
        work_queues = _WORK_QUEUES.validate_python(runtime["work_queues"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"Prefect runtime configuration is missing from {path}") from error

    if work_pool.type != ProcessWorker.type:
        raise ValueError(f"Local Prefect runtime requires a {ProcessWorker.type!r} work pool")
    if work_pool.concurrency_limit is None or work_pool.concurrency_limit < 1:
        raise ValueError("Prefect work pool concurrency limit must be positive")
    if not work_queues:
        raise ValueError("Prefect runtime requires at least one work queue")
    if any(queue.concurrency_limit is None or queue.concurrency_limit < 1 for queue in work_queues):
        raise ValueError("Prefect work queue concurrency limits must be positive")
    if any(queue.priority is None for queue in work_queues):
        raise ValueError("Prefect work queue priorities are required")

    names = [queue.name for queue in work_queues]
    priorities = [queue.priority for queue in work_queues]
    if len(names) != len(set(names)):
        raise ValueError("Prefect work queue names must be unique")
    if len(priorities) != len(set(priorities)):
        raise ValueError("Prefect work queue priorities must be unique")
    return work_pool, work_queues


async def _configure_queue(
    client: PrefectClient,
    work_pool_name: str,
    desired: WorkQueueCreate,
) -> None:
    try:
        current = await client.read_work_queue_by_name(
            desired.name,
            work_pool_name=work_pool_name,
        )
    except ObjectNotFound:
        try:
            await client.create_work_queue(
                name=desired.name,
                description=desired.description,
                is_paused=desired.is_paused,
                concurrency_limit=desired.concurrency_limit,
                priority=desired.priority,
                work_pool_name=work_pool_name,
            )
            return
        except ObjectAlreadyExists:
            current = await client.read_work_queue_by_name(
                desired.name,
                work_pool_name=work_pool_name,
            )

    updates = {
        field: value
        for field, value in (
            ("description", desired.description),
            ("is_paused", desired.is_paused),
            ("concurrency_limit", desired.concurrency_limit),
            ("priority", desired.priority),
        )
        if field in desired.model_fields_set and getattr(current, field) != value
    }
    if updates:
        await client.update_work_queue(current.id, **updates)


async def bootstrap_runtime(runtime: PrefectRuntime) -> None:
    work_pool, work_queues = runtime
    configured_pool = work_pool.model_copy(
        update={"base_job_template": ProcessWorker.get_default_base_job_template()}
    )
    async with get_client() as client:
        await client.create_work_pool(configured_pool, overwrite=True)
        for queue in work_queues:
            await _configure_queue(client, work_pool.name, queue)


def start_worker(work_pool: WorkPoolCreate) -> None:
    if work_pool.concurrency_limit is None:
        raise ValueError("Prefect worker requires a work pool concurrency limit")
    os.execvp(
        "prefect",
        (
            "prefect",
            "worker",
            "start",
            "--pool",
            work_pool.name,
            "--type",
            work_pool.type,
            "--limit",
            str(work_pool.concurrency_limit),
            "--no-create-pool-if-not-found",
            "--with-healthcheck",
        ),
    )


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bootstrap or run the local Prefect runtime.")
    parser.add_argument("action", choices=("bootstrap", "worker"))
    parsed = parser.parse_args(arguments)
    runtime = load_runtime()
    if parsed.action == "bootstrap":
        asyncio.run(bootstrap_runtime(runtime))
        return
    start_worker(runtime[0])


if __name__ == "__main__":
    main()
