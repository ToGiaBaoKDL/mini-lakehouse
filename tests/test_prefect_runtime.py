from pathlib import Path

import pytest

from mini_lakehouse.platform.prefect_runtime import load_runtime_policy, start_worker


def test_prefect_runtime_policy_matches_deployment_queues() -> None:
    policy = load_runtime_policy()

    assert policy.worker.concurrency_limit == 4
    assert policy.work_pool.concurrency_limit == 4
    assert {queue.name: queue.concurrency_limit for queue in policy.work_queues} == {
        "ingestion": 2,
        "transformation": 1,
        "processing": 1,
        "maintenance": 1,
    }


def test_prefect_runtime_policy_rejects_duplicate_queue_priorities(tmp_path: Path) -> None:
    policy = tmp_path / "prefect.yaml"
    policy.write_text(
        """
definitions:
  runtime:
    worker:
      concurrency_limit: 2
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
        load_runtime_policy(policy)


def test_prefect_worker_uses_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    invocation: list[str] = []

    def capture_exec(_executable: str, arguments: tuple[str, ...]) -> None:
        invocation.extend(arguments)

    monkeypatch.setattr("mini_lakehouse.platform.prefect_runtime.os.execvp", capture_exec)

    start_worker(load_runtime_policy())

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
