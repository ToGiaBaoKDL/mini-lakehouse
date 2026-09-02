import re
from pathlib import Path
from typing import Any

import yaml

NETDATA_ROOT = Path("sysops/netdata")


def _compose() -> dict[str, Any]:
    payload = yaml.safe_load((NETDATA_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_netdata_runtime_is_pinned_private_and_bounded() -> None:
    payload = _compose()
    assert set(payload["services"]) == {"netdata"}

    service = payload["services"]["netdata"]
    assert service["image"] == "${NETDATA_IMAGE:?NETDATA_IMAGE is required}"
    assert service["pid"] == "host"
    assert service["network_mode"] == "host"
    assert set(service["cap_add"]) == {"SYS_ADMIN", "SYS_PTRACE"}
    assert service["security_opt"] == ["apparmor:unconfined"]
    assert service["restart"] == "unless-stopped"
    assert service["deploy"]["resources"]["limits"] == {
        "cpus": "0.5",
        "memory": "512M",
    }
    assert service["environment"] == {
        "DISABLE_TELEMETRY": "1",
        "NETDATA_DISABLE_CLOUD": "1",
        "NETDATA_HEALTHCHECK_TARGET": "http://127.0.0.1:19999/api/v1/info",
    }
    assert "ports" not in service
    assert "privileged" not in service
    assert service["secrets"] == [
        {
            "source": "postgres_pgpass",
            "target": "postgres_pgpass",
            "uid": "0",
            "gid": "0",
            "mode": 0o400,
        }
    ]
    assert payload["secrets"] == {"postgres_pgpass": {"environment": "NETDATA_POSTGRES_PGPASS"}}

    volumes = set(service["volumes"])
    assert {
        "./config/go.d.conf:/etc/netdata/go.d.conf:ro",
        "./config/go.d:/etc/netdata/go.d:ro",
        "./config/health.d:/etc/netdata/health.d:ro",
        "./config/statsd.d:/etc/netdata/statsd.d:ro",
        "netdata-lib:/var/lib/netdata",
        "netdata-cache:/var/cache/netdata",
        "/:/host/root:ro,rslave",
        "/proc:/host/proc:ro",
        "/sys:/host/sys:ro",
        "/var/log:/host/var/log:ro",
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "/run/dbus:/run/dbus:ro",
    } <= volumes


def test_netdata_configuration_owns_retention_and_loopback_access() -> None:
    config = (NETDATA_ROOT / "config/netdata.conf").read_text(encoding="utf-8")

    for setting in (
        "db = dbengine",
        "update every = 1",
        "storage tiers = 3",
        "dbengine tier 0 retention size = 1GiB",
        "dbengine tier 0 retention time = 7d",
        "dbengine tier 1 retention size = 1GiB",
        "dbengine tier 1 retention time = 90d",
        "dbengine tier 2 retention size = 512MiB",
        "dbengine tier 2 retention time = 1y",
        "bind to = 127.0.0.1:19999",
        "enabled = yes",
    ):
        assert setting in config

    assert config.count("by dns = no") == 7
    assert "allow streaming from = !*" in config
    assert "allow netdata.conf from = !*" in config
    assert "bearer token protection = no" in config
    assert "provider = oci" in config
    assert "environment = dev" in config


def test_netdata_runs_only_the_reviewed_native_collectors() -> None:
    config = (NETDATA_ROOT / "config/netdata.conf").read_text(encoding="utf-8")
    go_config = yaml.safe_load((NETDATA_ROOT / "config/go.d.conf").read_text(encoding="utf-8"))

    assert "enable running new plugins = no" in config
    for plugin in (
        "apps",
        "charts.d",
        "debugfs",
        "otel",
        "otel-signal-viewer",
        "perf",
        "python.d",
        "systemd-units",
        "tc",
    ):
        assert f"{plugin} = no" in config
    for plugin in (
        "cgroups",
        "diskspace",
        "go.d",
        "network-viewer",
        "proc",
        "statsd",
        "systemd-journal",
        "timex",
    ):
        assert f"{plugin} = yes" in config

    assert go_config == {
        "enabled": True,
        "default_run": False,
        "max_procs": 1,
        "modules": {
            "docker": True,
            "httpcheck": True,
            "postgres": True,
            "systemdunits": True,
            "x509check": True,
        },
    }


def test_netdata_container_and_systemd_scope_is_bounded() -> None:
    config = (NETDATA_ROOT / "config/netdata.conf").read_text(encoding="utf-8")
    docker = yaml.safe_load((NETDATA_ROOT / "config/go.d/docker.conf").read_text(encoding="utf-8"))
    systemd = yaml.safe_load(
        (NETDATA_ROOT / "config/go.d/systemdunits.conf").read_text(encoding="utf-8")
    )
    discovery = yaml.safe_load(
        (NETDATA_ROOT / "config/go.d/sd/docker.conf").read_text(encoding="utf-8")
    )

    assert docker == {
        "jobs": [
            {
                "name": "local",
                "update_every": 30,
                "address": "unix:///var/run/docker.sock",
                "timeout": 10,
                "collect_container_size": False,
            }
        ]
    }
    assert systemd["jobs"] == [
        {
            "name": "lakehouse",
            "update_every": 10,
            "include": [
                "docker.service",
                "containerd.service",
                "tailscaled.service",
                "systemd-journald.service",
                "systemd-timesyncd.service",
                "lakehouse-metadata-backup.service",
                "lakehouse-metadata-backup.timer",
            ],
        }
    ]
    assert discovery["disabled"] is False
    assert discovery["discoverer"] == {
        "docker": {
            "address": "unix:///var/run/docker.sock",
            "timeout": "2s",
        }
    }
    assert len(discovery["services"]) == 1
    assert "*/docker-*.scope" in config
    assert "/system.slice/docker.service" in config
    assert "/system.slice/containerd.service" in config
    assert "/system.slice/tailscaled.service" in config
    assert "!netdata-netdata-1 !cloudflare-cloudflare-tunnel-1 *" in config
    for unavailable_context in (
        "/proc/net/stat/conntrack",
        "/proc/net/sctp/snmp",
        "/proc/net/ip_vs/stats",
        "/proc/net/stat/synproxy",
        "/proc/net/rpc/nfsd",
        "/proc/net/rpc/nfs",
        "/proc/spl/kstat/zfs/arcstats",
        "/sys/fs/btrfs",
    ):
        assert f"{unavailable_context} = no" in config
    assert "[plugin:proc:diskspace]" in config
    assert "/run/docker/netns/*" in config


def test_netdata_separates_origin_health_from_edge_certificates() -> None:
    http = yaml.safe_load((NETDATA_ROOT / "config/go.d/httpcheck.conf").read_text(encoding="utf-8"))
    certificates = yaml.safe_load(
        (NETDATA_ROOT / "config/go.d/x509check.conf").read_text(encoding="utf-8")
    )

    assert http["jobs"] == [
        {
            "name": "airflow_origin",
            "update_every": 10,
            "url": "http://127.0.0.1:8080/api/v2/version",
            "status_accepted": [200],
            "response_match": '"version"\\s*:',
            "timeout": 2,
        },
        {
            "name": "lightdash_origin",
            "update_every": 10,
            "url": "http://127.0.0.1:8081/api/v1/health",
            "status_accepted": [200],
            "timeout": 2,
        },
        {
            "name": "arxiv_lens_origin",
            "update_every": 10,
            "url": "http://127.0.0.1:8501/_stcore/health",
            "status_accepted": [200],
            "response_match": "^ok\\s*$",
            "timeout": 2,
        },
    ]
    assert certificates["jobs"] == [
        {
            "name": "airflow_edge",
            "update_every": 600,
            "source": "https://airflow.tgblab.io.vn:443",
            "timeout": 5,
        },
        {
            "name": "analytics_edge",
            "update_every": 600,
            "source": "https://analytics.tgblab.io.vn:443",
            "timeout": 5,
        },
        {
            "name": "arxiv_lens_edge",
            "update_every": 600,
            "source": "https://arxiv.tgblab.io.vn:443",
            "timeout": 5,
        },
        {
            "name": "netdata_edge",
            "update_every": 600,
            "source": "https://netdata.tgblab.io.vn:443",
            "timeout": 5,
        },
    ]
    assert "observe.tgblab.io.vn" not in str(certificates)


def test_netdata_postgres_discovery_is_exact_and_uses_a_secret_file() -> None:
    discovery_text = (NETDATA_ROOT / "config/go.d/sd/docker.conf").read_text(encoding="utf-8")
    discovery = yaml.safe_load(discovery_text)
    rule = discovery["services"][0]
    deploy = (NETDATA_ROOT / "deploy").read_text(encoding="utf-8")

    assert rule["id"] == "metadata-postgres"
    assert 'eq .PrivatePort "5432"' in rule["match"]
    assert '"com.docker.compose.project") "metadata-postgres"' in rule["match"]
    assert '"com.docker.compose.service") "metadata-postgres"' in rule["match"]
    assert "module: postgres" in rule["config_template"]
    assert "update_every: 10" in rule["config_template"]
    assert "passfile=/run/secrets/postgres_pgpass" in rule["config_template"]
    assert (
        "collect_databases_matching: 'postgres airflow lightdash t0_trading'"
        in rule["config_template"]
    )
    assert "password" not in discovery_text.lower()
    assert "PG_MONITOR_PASSWORD" not in deploy
    assert "NETDATA_POSTGRES_PGPASS" in deploy
    assert "unset escaped_password monitor_password monitor_secret" in deploy
    assert "metadata-postgres/pg_monitor" in deploy
    assert 'infra/runtime/postgres/deploy" pg_monitor' in deploy
    assert 'select(.plugin == "go.d" and .module == "postgres")' in deploy
    assert 'select(.plugin == "go.d" and .module == "docker")' in deploy
    assert '.functions["postgres:top-queries"].access' in deploy
    assert '.functions["postgres:running-queries"].access' in deploy
    assert '["signed-in", "same-space", "sensitive-data"]' in deploy


def test_netdata_airflow_statsd_is_private_bounded_and_unit_explicit() -> None:
    config = (NETDATA_ROOT / "config/netdata.conf").read_text(encoding="utf-8")
    profile = (NETDATA_ROOT / "config/statsd.d/airflow.conf").read_text(encoding="utf-8")
    airflow = _compose()["services"]["netdata"]

    assert "statsd = yes" in config
    assert "bind to = udp:172.24.0.1:8125" in config
    assert "create private charts for metrics matching = !*" in config
    assert "max private charts hard limit = 100" in config
    assert "histograms and timers percentile (percentThreshold) = 95" in config
    assert "tcp:" not in config
    assert airflow["network_mode"] == "host"
    assert "./config/statsd.d:/etc/netdata/statsd.d:ro" in airflow["volumes"]

    assert "metrics = airflow.*" in profile
    assert "private charts = no" in profile
    assert "gaps when not collected = yes" in profile
    for context, units in (
        ("airflow.heartbeats", "heartbeats/s"),
        ("airflow.scheduler_tasks", "tasks"),
        ("airflow.running_dag_runs", "runs"),
        ("airflow.dag_file_queue", "files"),
        ("airflow.executor_tasks", "tasks"),
        ("airflow.executor_slots", "slots"),
        ("airflow.task_duration", "ms"),
        ("airflow.task_queue_duration", "ms"),
        ("airflow.dag_run_duration", "ms"),
        ("airflow.dag_run_delay", "ms"),
        ("airflow.scheduler_duration", "ms"),
    ):
        assert f"context = {context}" in profile
        section = profile[profile.index(f"context = {context}") :]
        assert f"units = {units}" in section.split("\n\n", 1)[0]

    for metric in (
        "scheduler_heartbeat",
        "dag_processor_heartbeat",
        "scheduler.tasks.executable",
        "scheduler.tasks.starving",
        "scheduler.dagruns.running",
        "dag_processing.file_path_queue_size",
        "executor.running_tasks",
        "executor.queued_tasks",
        "executor.open_slots",
        "task.duration",
        "task.scheduled_duration",
        "task.queued_duration",
        "dagrun.duration.success",
        "dagrun.duration.failed",
        "dagrun.schedule_delay",
        "dagrun.first_task_scheduling_delay",
        "dagrun.first_task_start_delay",
        "scheduler.scheduler_loop_duration",
        "scheduler.critical_section_duration",
        "scheduler.critical_section_query_duration",
        "scheduler.executor_heartbeat_duration",
    ):
        assert f"airflow.{metric}" in profile
    assert "airflow.pool." not in profile

    for title in (
        "Active DAG runs",
        "Queued DAG files",
        "Open executor slots",
        "Observed task duration (p95)",
        "Observed task state duration (p95)",
        "Observed DAG run duration by outcome (p95)",
        "Observed DAG run scheduling delay (p95)",
        "Observed scheduler internal duration (p95)",
    ):
        assert f"title = {title}" in profile


def test_netdata_overrides_the_sticky_postgres_rollback_alert() -> None:
    alert = (NETDATA_ROOT / "config/health.d/postgres.conf").read_text(encoding="utf-8")
    volumes = set(_compose()["services"]["netdata"]["volumes"])

    assert "./config/health.d:/etc/netdata/health.d:ro" in volumes
    assert "template: postgres_db_transactions_rollback_ratio" in alert
    assert "on: postgres.db_transactions_ratio" in alert
    assert "lookup: average -5m unaligned of rollback" in alert
    assert "warn: $this > (($status >= $WARNING) ? (1) : (2))" in alert
    assert "delay: down 15m multiplier 1.5 max 1h" in alert


def test_netdata_statsd_network_and_firewall_are_exact_and_persistent() -> None:
    network = Path("infra/runtime/host/reconcile-docker-networks").read_text(encoding="utf-8")
    firewall = Path("infra/runtime/host/netdata-statsd-firewall").read_text(encoding="utf-8")
    reconcile = Path("infra/runtime/host/reconcile-netdata-statsd").read_text(encoding="utf-8")
    unit = Path("infra/runtime/host/systemd/lakehouse-netdata-statsd.service").read_text(
        encoding="utf-8"
    )
    host_action = Path(".github/actions/reconcile-services-host/action.yml").read_text(
        encoding="utf-8"
    )

    for source in (network, firewall):
        assert "172.24.0.0/16" in source
        assert "172.24.0.1" in source
        assert "{{len .IPAM.Config}}" in source
        assert "{{(index .IPAM.Config 0).Subnet}}" in source
        assert "{{(index .IPAM.Config 0).Gateway}}" in source
        assert "{{json .IPAM.Config}}" not in source
        assert ".IPRange" not in source
    assert '--subnet "$subnet"' in network
    assert '--gateway "$gateway"' in network
    assert "unexpected IPAM" in network
    assert "LAKEHOUSE-NETDATA" in firewall
    assert '-i "$bridge" -s "$subnet" -d "$gateway"' in firewall
    assert "-p udp --dport 8125 -j ACCEPT" in firewall
    assert 'iptables -w 10 -I INPUT 1 -j "$chain"' in firewall
    assert "0.0.0.0" not in firewall
    assert "systemctl enable" in reconcile
    assert "systemctl restart" in reconcile
    assert "ExecStart=/usr/local/sbin/lakehouse-netdata-statsd-firewall" in unit
    assert "WantedBy=multi-user.target docker.service" in unit
    assert "Reconcile Netdata StatsD firewall" in host_action
    assert "infra/runtime/host/netdata-statsd-firewall" in host_action


def test_netdata_has_one_deploy_only_protected_workflow() -> None:
    workflow = Path(".github/workflows/deploy-netdata.yml").read_text(encoding="utf-8")
    action = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
    dispatcher = Path("infra/runtime/delivery/deploy-component").read_text(encoding="utf-8")
    deploy = (NETDATA_ROOT / "deploy").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "environment: dev" in workflow
    assert "group: dev-netdata" in workflow
    assert "make netdata-validate" in workflow
    assert "component: netdata" in workflow
    assert "sysops/netdata/**" in workflow
    assert "docker/build-push-action" not in workflow
    assert "amazon-ecr-login" not in workflow
    assert "sysops/netdata" in action
    assert "netdata/netdata:v" in action
    assert 'deploy="$bundle_root/sysops/netdata/deploy"' in dispatcher
    assert "aws ecr" not in deploy.lower()
    assert "statsd_sockets=$(ss -lunH" in deploy
    assert 'test "$statsd_sockets" = "172.24.0.1:8125"' in deploy
    assert "airflow.heartbeats" in deploy
    assert "airflow.scheduler_tasks" in deploy
    assert "airflow.task_duration" in deploy
    assert "NETDATA_RUNTIME_ROOT" in deploy
    assert 'release_dir="$runtime_root/releases/$release_sha256"' in deploy
    assert 'startswith($release_dir + "/")' in deploy
    assert "Netdata configuration is not mounted from its persistent release." in deploy
    assert "x509check performs a connection check immediately" in deploy
    assert "for job in airflow_edge analytics_edge arxiv_lens_edge netdata_edge" in deploy
    assert "collector=x509check" in deploy
    assert "--network lakehouse-observability" in deploy
    assert "--pull never --read-only --cap-drop ALL" in deploy
    assert "docker exec -i" not in deploy


def test_netdata_release_and_deploy_are_immutable_and_secret_safe() -> None:
    image = (NETDATA_ROOT / "image").read_text(encoding="utf-8").strip()
    deploy = (NETDATA_ROOT / "deploy").read_text(encoding="utf-8")

    assert re.fullmatch(r"netdata/netdata:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", image)
    assert ":latest" not in image
    assert ":stable" not in image
    assert 'docker pull "$image"' in deploy
    assert "find config -type f -print" in deploy
    assert "LC_ALL=C sort" in deploy
    assert "sha256sum deploy" in deploy
    assert 'find "$staging_dir/config" -type d -exec chmod 0755 {} +' in deploy
    assert 'find "$staging_dir/config" -type f -exec chmod 0644 {} +' in deploy
    assert "--force-recreate --wait --wait-timeout 120" in deploy
    assert "http://127.0.0.1:19999/api/v1/info" in deploy
    assert ".mirrored_hosts | index($hostname)" in deploy
    assert "mcp_dev_preview_api_key" in deploy
    assert 'management_token=$(docker exec "$container_id" cat' in deploy
    assert "health_api RESET" in deploy
    assert 'health_state=$(health_api "LIST")' in deploy
    assert '.type == "None"' in deploy
    assert "curl --config -" in deploy
    assert "printf '%s' \"$management_token\"" not in deploy
    assert "AWS_ACCESS_KEY_ID=" not in deploy
    assert "AWS_SECRET_ACCESS_KEY=" not in deploy
    assert "AWS_SHARED_CREDENTIALS_FILE=/dev/null" in deploy
    assert 'AWS_CONFIG_FILE="$host_config"' in deploy
    assert "aws ecr" not in deploy.lower()


def test_netdata_keeps_health_active_without_external_notifications() -> None:
    notification_config = (NETDATA_ROOT / "config/health_alarm_notify.conf").read_text(
        encoding="utf-8"
    )
    methods = [line for line in notification_config.splitlines() if line.startswith("SEND_")]

    assert len(methods) == 31
    assert all(line.endswith('="NO"') for line in methods)
    assert 'SEND_EMAIL="NO"' in methods
    assert 'SEND_SYSLOG="NO"' in methods
    assert 'SEND_CUSTOM="NO"' in methods
