import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from prefect.client.schemas.actions import WorkPoolCreate, WorkQueueCreate
from prefect.workers.process import ProcessWorker

from orchestration.runtime import (
    bootstrap_runtime,
    load_runtime,
    start_worker,
)


def test_prefect_runtime_uses_official_prefect_action_models() -> None:
    work_pool, work_queues = load_runtime()

    assert isinstance(work_pool, WorkPoolCreate)
    assert all(isinstance(queue, WorkQueueCreate) for queue in work_queues)
    assert work_pool.concurrency_limit == 4
    assert {queue.name: queue.concurrency_limit for queue in work_queues} == {
        "ingestion": 2,
        "transformation": 1,
        "processing": 1,
        "maintenance": 1,
    }


def test_prefect_runtime_rejects_duplicate_queue_priorities(tmp_path: Path) -> None:
    runtime = tmp_path / "prefect.yaml"
    runtime.write_text(
        """
definitions:
  runtime:
    work_pool:
      name: local
      type: process
      concurrency_limit: 2
    work_queues:
      - {name: first, priority: 1, concurrency_limit: 1}
      - {name: second, priority: 1, concurrency_limit: 1}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="priorities must be unique"):
        load_runtime(runtime)


def test_prefect_runtime_bootstrap_uses_pool_upsert_and_skips_unchanged_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_pool, work_queues = load_runtime()
    client = SimpleNamespace(
        create_work_pool=AsyncMock(),
        read_work_queue_by_name=AsyncMock(),
        create_work_queue=AsyncMock(),
        update_work_queue=AsyncMock(),
    )
    current_queues = {
        queue.name: SimpleNamespace(
            id=uuid4(),
            description=queue.description,
            is_paused=queue.is_paused,
            concurrency_limit=queue.concurrency_limit,
            priority=queue.priority,
        )
        for queue in work_queues
    }

    async def read_queue(name: str, *, work_pool_name: str) -> SimpleNamespace:
        assert work_pool_name == work_pool.name
        return current_queues[name]

    client.read_work_queue_by_name.side_effect = read_queue

    @asynccontextmanager
    async def client_context():
        yield client

    monkeypatch.setattr(
        "orchestration.runtime.get_client",
        client_context,
    )

    asyncio.run(bootstrap_runtime((work_pool, work_queues)))

    configured_pool = client.create_work_pool.await_args.args[0]
    assert configured_pool.base_job_template == ProcessWorker.get_default_base_job_template()
    assert client.create_work_pool.await_args.kwargs == {"overwrite": True}
    assert client.read_work_queue_by_name.await_count == len(work_queues)
    client.create_work_queue.assert_not_awaited()
    client.update_work_queue.assert_not_awaited()


def test_prefect_worker_uses_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    invocation: list[str] = []

    def capture_exec(_executable: str, arguments: tuple[str, ...]) -> None:
        invocation.extend(arguments)

    monkeypatch.setattr("orchestration.runtime.os.execvp", capture_exec)

    work_pool, _ = load_runtime()
    start_worker(work_pool)

    assert invocation == [
        "prefect",
        "worker",
        "start",
        "--pool",
        "lakehouse-process",
        "--type",
        "process",
        "--limit",
        "4",
        "--no-create-pool-if-not-found",
        "--with-healthcheck",
    ]
