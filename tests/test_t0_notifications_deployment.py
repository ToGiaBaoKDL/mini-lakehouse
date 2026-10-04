"""Execute deployment with mocked AWS/Docker/systemd; no production or Telegram access."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

import pytest


class CommandEvent(TypedDict):
    command: str
    args: list[str]
    identity: str
    delivery_sha256: str
    telegram_sha256: str


@dataclass
class Deployment:
    root: Path
    env: dict[str, str]
    events_path: Path

    @property
    def events(self) -> list[CommandEvent]:
        if not self.events_path.exists():
            return []
        return [
            cast(CommandEvent, json.loads(row)) for row in self.events_path.read_text().splitlines()
        ]

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.root / "t0-trading/deploy" / script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    root = tmp_path / "bundle"
    shutil.copytree("t0-trading/deploy", root / "t0-trading/deploy")
    shutil.copytree("infra/runtime/postgres", root / "infra/runtime/postgres")
    window = root / "t0-trading/deploy/stream-window"
    window.write_text('#!/bin/sh\nexit "${T0_TEST_WINDOW_EXIT:-1}"\n')
    window.chmod(0o755)
    identity_dir = tmp_path / "identity"
    for name in ("t0-trading", "services-deployer", "metadata-postgres"):
        directory = identity_dir / name
        directory.mkdir(parents=True)
        (directory / "host-config").write_text("test-only")
        (directory / "config").write_text("test-only")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runner = fake_bin / "runner"
    runner.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, sys
from hashlib import sha256
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
event = {"command": command, "args": args,
         "identity": Path(os.environ.get("AWS_CONFIG_FILE", "")).parent.name,
         "delivery_sha256": "", "telegram_sha256": ""}
if command == "docker" and "create" in args and "notifications" in args:
    assert "T0_DELIVERY_DSN" not in os.environ
    assert "T0_TELEGRAM_SECRET" not in os.environ
    for field, source in (("delivery", "T0_DELIVERY_DSN_HOST_FILE"),
                          ("telegram", "T0_TELEGRAM_HOST_FILE")):
        event[field + "_sha256"] = sha256(Path(os.environ[source]).read_bytes()).hexdigest()
with Path(os.environ["T0_TEST_EVENTS"]).open("a") as output:
    output.write(json.dumps(event) + "\\n")
if command == "aws":
    if args[:2] == ["ssm", "get-parameter"]:
        name = args[args.index("--name") + 1]
        print(os.environ["T0_TEST_ENABLED"] if name.endswith("notifications_enabled")
              else "s3://test-landing")
    elif args[:2] == ["secretsmanager", "get-secret-value"]:
        name = args[args.index("--secret-id") + 1]
        if name.endswith("/telegram"):
            print(json.dumps({"bot_token": "fixture-bot-secret",
                              "chat_id": "" if os.environ.get("T0_TEST_BAD_SECRET") else "123"}))
        else:
            print(json.dumps({"version": 1, "password": "" if os.environ.get("T0_TEST_BAD_DB")
                              else "fixture-password:@ /'\\\\?"}))
    elif args[:2] == ["secretsmanager", "put-secret-value"]:
        json.load(sys.stdin)
    elif args[:2] != ["secretsmanager", "describe-secret"]:
        sys.exit(2)
elif command == "systemctl":
    sys.exit(0 if os.environ.get("T0_TEST_ACTIVE_SERVICES") else 1)
elif command == "docker" and "metadata-postgres-bootstrap" in args:
    if os.environ.get("T0_TEST_BOOTSTRAP_FAIL"):
        sys.exit(1)
"""
    )
    runner.chmod(0o755)
    for name in ("aws", "docker", "systemctl", "sudo"):
        (fake_bin / name).symlink_to(runner)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "AWS_IDENTITY_DIR": str(identity_dir),
        "LAKEHOUSE_ENVIRONMENT": "dev",
        "T0_STREAM_SPOOL_DIR": str(tmp_path / "spool"),
        "T0_TEST_EVENTS": str(tmp_path / "events.jsonl"),
        "T0_TEST_ENABLED": "false",
    }
    for key in (
        "T0_TEST_BAD_SECRET",
        "T0_TEST_BAD_DB",
        "T0_TEST_ACTIVE_SERVICES",
        "T0_TEST_WINDOW_EXIT",
        "T0_TEST_BOOTSTRAP_FAIL",
        "T0_DELIVERY_DSN",
        "T0_TELEGRAM_SECRET",
    ):
        env.pop(key, None)
    return Deployment(root, env, tmp_path / "events.jsonl")


def test_disabled_deploy_creates_only_capture_without_secret_or_db_access(
    deployment: Deployment,
) -> None:
    result = deployment.run("reconcile", "t0-trading:test")
    assert result.returncode == 0, result.stderr
    assert not any("secretsmanager" in event["args"] for event in deployment.events)
    docker = [event["args"] for event in deployment.events if event["command"] == "docker"]
    assert len(docker) == 1 and "create" in docker[0]
    assert "notifications" not in docker[0]
    assert "up" not in docker[0]
    assert not (Path(deployment.env["HOME"]) / ".config/lakehouse/dev/secrets/t0-trading").exists()


def test_enabled_deploy_uses_compose_secrets_bootstraps_and_creates_both(
    deployment: Deployment,
) -> None:
    deployment.env.update(T0_TEST_ENABLED="true", T0_TEST_ACTIVE_SERVICES="1")
    result = deployment.run("reconcile", "t0-trading:test")
    assert result.returncode == 0, result.stderr
    assert "fixture-bot-secret" not in result.stdout + result.stderr
    secret_dir = Path(deployment.env["HOME"]) / ".config/lakehouse/dev/secrets/t0-trading"
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    assert sorted(path.name for path in secret_dir.iterdir()) == ["delivery-dsn", "telegram.json"]
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in secret_dir.iterdir())
    reads = [event for event in deployment.events if "get-secret-value" in event["args"]]
    telegram = next(
        event for event in reads if any(arg.endswith("/telegram") for arg in event["args"])
    )
    assert telegram["identity"] == "services-deployer"
    assert all(event["identity"] == "metadata-postgres" for event in reads if event != telegram)
    docker = [event["args"] for event in deployment.events if event["command"] == "docker"]
    assert any("metadata-postgres-bootstrap" in args for args in docker)
    capture = next(args for args in docker if "create" in args)
    assert "--profile" in capture and "notifications" in capture
    assert any(arg.endswith("/notifications.compose.yaml") for arg in capture)
    assert not any("start" in event["args"] for event in deployment.events)
    prepared = next(event for event in deployment.events if event["args"] == capture)
    expected_dsn = "postgresql://t0_trading:fixture-password%3A%40%20%2F%27%5C%3F@metadata-postgres:5432/t0_trading"
    assert prepared["delivery_sha256"] == sha256(expected_dsn.encode()).hexdigest()
    expected_telegram = '{"bot_token":"fixture-bot-secret","chat_id":"123"}'
    assert prepared["telegram_sha256"] == sha256(expected_telegram.encode()).hexdigest()


def test_market_window_refuses_before_aws_or_runtime_mutations(deployment: Deployment) -> None:
    deployment.env.update(T0_TEST_ENABLED="true", T0_TEST_WINDOW_EXIT="0")
    result = deployment.run("reconcile", "t0-trading:test")
    assert result.returncode == 75
    assert deployment.events == []


@pytest.mark.parametrize("bad_setting", ["invalid_flag", "invalid_secret", "invalid_database"])
def test_invalid_activation_or_credentials_cannot_create_or_bootstrap_runtime(
    deployment: Deployment,
    bad_setting: str,
) -> None:
    deployment.env["T0_TEST_ENABLED"] = "unknown" if bad_setting == "invalid_flag" else "true"
    if bad_setting == "invalid_secret":
        deployment.env["T0_TEST_BAD_SECRET"] = "1"
    if bad_setting == "invalid_database":
        deployment.env["T0_TEST_BAD_DB"] = "1"
    result = deployment.run("reconcile", "t0-trading:test")
    assert result.returncode != 0
    assert "fixture-bot-secret" not in result.stdout + result.stderr
    assert all(event["command"] == "aws" for event in deployment.events)


@pytest.mark.parametrize("failure", ["credentials", "bootstrap", "window"])
def test_failed_preflight_preserves_existing_files_and_cleans_staging(
    deployment: Deployment, failure: str
) -> None:
    deployment.env.update(T0_TEST_ENABLED="true", T0_TEST_ACTIVE_SERVICES="1")
    secret_dir = Path(deployment.env["HOME"]) / ".config/lakehouse/dev/secrets/t0-trading"
    secret_dir.mkdir(parents=True, mode=0o700)
    for name in ("delivery-dsn", "telegram.json"):
        (secret_dir / name).write_text("previous-credential")
        (secret_dir / name).chmod(0o600)
    if failure == "credentials":
        deployment.env["T0_TEST_BAD_DB"] = "1"
    elif failure == "bootstrap":
        deployment.env["T0_TEST_BOOTSTRAP_FAIL"] = "1"
    else:
        # Window opens after credential staging/bootstrap, before consumers can be stopped.
        window = deployment.root / "t0-trading/deploy/stream-window"
        window.write_text(
            '#!/bin/sh\ncount_file="$HOME/window-checks"\n'
            'count=0\nif [ -f "$count_file" ]; then read -r count <"$count_file"; fi\n'
            'count=$((count + 1))\nprintf "%s\\n" "$count" >"$count_file"\n'
            '[ "$count" -ge 3 ]\n'
        )
    result = deployment.run("reconcile")
    assert result.returncode != 0
    assert sorted(path.name for path in secret_dir.iterdir()) == ["delivery-dsn", "telegram.json"]
    assert all(path.read_text() == "previous-credential" for path in secret_dir.iterdir())
    assert not any(event["command"] == "sudo" for event in deployment.events)
    assert not any("create" in event["args"] for event in deployment.events)
    assert "fixture-bot-secret" not in result.stdout + result.stderr


def test_redeploy_refreshes_private_sources_without_starting_consumers(
    deployment: Deployment,
) -> None:
    deployment.env["T0_TEST_ENABLED"] = "true"
    first = deployment.run("reconcile")
    assert first.returncode == 0, first.stderr
    secret_dir = Path(deployment.env["HOME"]) / ".config/lakehouse/dev/secrets/t0-trading"
    for path in secret_dir.iterdir():
        path.write_text("previous-credential")
    second = deployment.run("reconcile")
    assert second.returncode == 0, second.stderr
    assert all(path.read_text() != "previous-credential" for path in secret_dir.iterdir())
    assert not any("start" in event["args"] for event in deployment.events)


@pytest.mark.parametrize("valid_payload", [False, True])
def test_secret_sync_rejects_template_and_never_exposes_payload(
    deployment: Deployment,
    tmp_path: Path,
    valid_payload: bool,
) -> None:
    payload = tmp_path / "telegram.json"
    payload.write_text(
        json.dumps({"bot_token": "test-secret" if valid_payload else "", "chat_id": "123"})
    )
    payload.chmod(0o600)
    result = deployment.run("sync-telegram-secret", str(payload))
    assert (result.returncode == 0) == valid_payload
    assert "test-secret" not in result.stdout + result.stderr
    puts = [event for event in deployment.events if "put-secret-value" in event["args"]]
    assert len(puts) == int(valid_payload)
    if valid_payload:
        assert "file:///dev/stdin" in puts[0]["args"]


def test_secret_sync_refuses_group_readable_credentials(
    deployment: Deployment, tmp_path: Path
) -> None:
    payload = tmp_path / "telegram.json"
    payload.write_text('{"bot_token":"test-secret", "chat_id":"123"}')
    payload.chmod(0o640)
    result = deployment.run("sync-telegram-secret", str(payload))
    assert result.returncode != 0
    assert deployment.events == []
