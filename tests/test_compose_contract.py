import json
from pathlib import Path
from typing import Any

import yaml

AIRFLOW_COMPOSE = "automation/airflow/deploy/compose.yaml"
ARXIV_LENS_COMPOSE = "arxiv-lens/deploy/compose.yaml"
LIGHTDASH_COMPOSE = "analytics/lightdash/deploy/compose.yaml"
POSTGRES_COMPOSE = "infra/runtime/postgres/compose.yaml"
CLOUDFLARE_COMPOSE = "infra/runtime/cloudflare/compose.yaml"
AIRFLOW_PROJECT = Path("automation/airflow")
AIRFLOW_RUNTIME = AIRFLOW_PROJECT / "runtime"


def _compose(path: str) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_compose_owns_only_self_hosted_application_services() -> None:
    assert not list(Path.cwd().glob("compose*.yaml"))

    airflow = _compose(AIRFLOW_COMPOSE)["services"]
    assert set(airflow) == {
        "airflow-volumes-init",
        "airflow-init",
        "airflow-api-server",
        "airflow-scheduler",
        "airflow-dag-processor",
    }
    assert set(_compose(ARXIV_LENS_COMPOSE)["services"]) == {"arxiv-lens"}
    assert set(_compose(LIGHTDASH_COMPOSE)["services"]) == {"lightdash"}
    assert set(_compose(POSTGRES_COMPOSE)["services"]) == {
        "metadata-postgres",
        "metadata-postgres-bootstrap",
    }
    assert set(_compose(CLOUDFLARE_COMPOSE)["services"]) == {"cloudflare-tunnel"}


def test_airflow_uses_local_executor_and_required_runtime_components() -> None:
    payload = _compose(AIRFLOW_COMPOSE)
    common = payload["x-airflow-common"]
    environment = common["environment"]

    assert environment["AIRFLOW__CORE__EXECUTOR"] == "LocalExecutor"
    assert environment["AIRFLOW__CORE__LOAD_EXAMPLES"] == "false"
    assert environment["AIRFLOW__CORE__RERUN_WITH_LATEST_VERSION"] == "true"
    assert common["image"] == "${AIRFLOW_IMAGE:-airflow:local}"
    assert common["user"] == "${LOCAL_UID}:0"
    assert "build" not in common
    assert environment["AIRFLOW__SECRETS__BACKEND"] == ("airflow_runtime.secrets.AwsSecretsBackend")
    assert "variables_prefix" in environment["AIRFLOW__SECRETS__BACKEND_KWARGS"]
    assert "profile_name" not in environment["AIRFLOW__SECRETS__BACKEND_KWARGS"]
    assert payload["services"]["airflow-dag-processor"]["command"] == "airflow dag-processor"
    bundle_config = json.loads(environment["AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST"])
    assert bundle_config == [
        {
            "name": "automation",
            "classpath": "airflow.providers.git.bundles.git.GitDagBundle",
            "kwargs": {
                "repo_url": "https://github.com/ToGiaBaoKDL/mini-lakehouse.git",
                "tracking_ref": "main",
                "subdir": "automation/airflow/bundle",
                "sparse_dirs": ["automation/airflow/bundle"],
                "refresh_interval": 60,
            },
        }
    ]
    assert environment["AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_STORAGE_PATH"] == (
        "/opt/airflow/dag-bundles"
    )
    assert environment["AIRFLOW__DAG_PROCESSOR__DISABLE_BUNDLE_VERSIONING"] == "false"
    init_command = payload["services"]["airflow-init"]["command"]
    volumes_init = payload["services"]["airflow-volumes-init"]
    assert volumes_init["user"] == "0:0"
    assert "chmod -R g+rwX" in volumes_init["command"][-1]
    assert payload["services"]["airflow-init"]["depends_on"] == {
        "airflow-volumes-init": {"condition": "service_completed_successfully"}
    }
    assert init_command[:2] == ["bash", "-ec"]
    assert "install -m 0600 /run/secrets/airflow_admin_passwords" in init_command[-1]
    assert "exec airflow db migrate" in init_command[-1]
    assert all("./orchestration:" not in volume for volume in common["volumes"])
    assert all("dist/" not in volume for volume in common["volumes"])
    scheduler = payload["services"]["airflow-scheduler"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in scheduler["volumes"]
    assert scheduler["group_add"] == ["${DOCKER_GID}"]
    assert environment["HOST_AWS_IDENTITY_DIR"] == "${AWS_IDENTITY_DIR}"
    assert "OCR_AWS_PROFILE" not in environment
    assert "DBT_AWS_PROFILE" not in environment
    assert "OCR_TASK_IMAGE" not in environment
    assert "DOCKER_TASK_USER" not in environment
    assert environment["AIRFLOW_CONN_AWS_DEFAULT"] == "aws://"
    assert "AIRFLOW_BOOTSTRAP_VERSION" not in environment
    assert "AWS_REGION" not in environment
    assert environment["PYTHONWARNINGS"] == "ignore:ProvidersManager.hooks is deprecated"
    assert environment["AIRFLOW__LOGGING__REMOTE_LOGGING"] == "true"
    assert environment["AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER"] == ("${AIRFLOW_REMOTE_LOG_URI}")
    assert environment["AIRFLOW__LOGGING__DELETE_LOCAL_LOGS"] == "true"
    assert "AIRFLOW__LOGGING__LOGGING_CONFIG_CLASS" not in environment
    assert environment["AIRFLOW__LOGGING__LOGGING_LEVEL"] == "INFO"
    assert environment["AIRFLOW__LOGGING__JSON_LOGS"] == "true"
    assert environment["AIRFLOW__API__BASE_URL"] == ("${AIRFLOW_BASE_URL:-http://127.0.0.1:8080}")
    assert environment["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE"].startswith(
        "/opt/airflow/auth/"
    )
    assert not environment["AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE"].endswith(
        ".generated"
    )
    assert "airflow-auth:/opt/airflow/auth" in common["volumes"]
    assert "airflow-dag-bundles:/opt/airflow/dag-bundles" in common["volumes"]
    assert payload["networks"]["metadata"]["external"] is True
    assert set(common["networks"]) == {"metadata", "runtime", "telemetry"}
    assert payload["networks"]["telemetry"] == {
        "external": True,
        "name": "lakehouse-observability",
    }
    assert _compose(POSTGRES_COMPOSE)["networks"]["metadata"]["internal"] is True


def test_airflow_runtime_secrets_are_service_scoped_files() -> None:
    payload = _compose(AIRFLOW_COMPOSE)
    services = payload["services"]
    environment = payload["x-airflow-common"]["environment"]

    assert "env_file" not in payload["x-airflow-common"]
    assert "env_file" not in services["airflow-init"]
    assert "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN" not in environment
    assert "AIRFLOW__CORE__FERNET_KEY" not in environment
    assert "AIRFLOW__API_AUTH__JWT_SECRET" not in environment
    assert environment["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD"].startswith("python -c")
    assert "sys.stdout.write" in environment["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD"]
    assert "postgresql+psycopg://" in environment["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_CMD"]
    assert environment["AIRFLOW__CORE__FERNET_KEY_CMD"].startswith("cat /run/secrets/")
    assert environment["AIRFLOW__API_AUTH__JWT_SECRET_CMD"].startswith("cat /run/secrets/")
    assert set(payload["secrets"]) == {
        "airflow_database_password",
        "airflow_fernet_key",
        "airflow_jwt_secret",
        "airflow_admin_passwords",
    }
    assert all("environment" in secret for secret in payload["secrets"].values())

    services_makefile = Path("make/services.mk").read_text(encoding="utf-8")
    airflow_secrets = Path("automation/airflow/deploy/initialize-secrets").read_text(
        encoding="utf-8"
    )
    postgres_secrets = Path("infra/runtime/postgres/initialize-secrets").read_text(encoding="utf-8")
    airflow_reconcile = Path("automation/airflow/deploy/reconcile").read_text(encoding="utf-8")
    assert '"version":1' in airflow_secrets + postgres_secrets
    assert ".version == 1" in airflow_secrets + postgres_secrets
    assert "admin_password" in airflow_secrets
    assert "AIRFLOW_ADMIN_PASSWORDS" in airflow_reconcile
    assert "infra/runtime/postgres/initialize-secrets" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets bootstrap" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets airflow" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets lightdash" in services_makefile
    assert "automation/airflow/deploy/initialize-secrets" in services_makefile
    assert "secretsmanager put-secret-value" not in services_makefile


def test_airflow_runtime_components_have_role_appropriate_healthchecks() -> None:
    services = _compose(AIRFLOW_COMPOSE)["services"]

    assert "/api/v2/version" in services["airflow-api-server"]["healthcheck"]["test"][-1]
    assert "SchedulerJob" in services["airflow-scheduler"]["healthcheck"]["test"][-1]
    assert "DagProcessorJob" in services["airflow-dag-processor"]["healthcheck"]["test"][-1]
    assert "healthcheck" not in services["airflow-init"]
    postgres = _compose(POSTGRES_COMPOSE)["services"]
    assert "healthcheck" in postgres["metadata-postgres"]
    bootstrap = postgres["metadata-postgres-bootstrap"]
    assert bootstrap["restart"] == "no"
    assert set(bootstrap["secrets"]) == {
        "application_database_password",
        "postgres_password",
    }
    assert "${POSTGRES_APPLICATION}.sql" in bootstrap["command"][-1]
    assert set(_compose(POSTGRES_COMPOSE)["secrets"]) == {
        "application_database_password",
        "postgres_password",
    }
    assert {path.name for path in Path("infra/runtime/postgres/bootstrap").glob("*.sql")} == {
        "airflow.sql",
        "lightdash.sql",
        "pg_monitor.sql",
    }
    for path in ("airflow.sql", "lightdash.sql"):
        source = (Path("infra/runtime/postgres/bootstrap") / path).read_text(encoding="utf-8")
        database = Path(path).stem
        assert "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC" in source
        assert f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC" in source
        assert f"GRANT CONNECT ON DATABASE {database} TO {database}" in source
        assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in source

    pg_monitor_bootstrap = Path("infra/runtime/postgres/bootstrap/pg_monitor.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE ROLE lakehouse_monitor" in pg_monitor_bootstrap
    assert "GRANT CONNECT ON DATABASE postgres TO lakehouse_monitor" in pg_monitor_bootstrap
    assert "GRANT pg_monitor TO lakehouse_monitor" in pg_monitor_bootstrap
    assert "CONNECTION LIMIT 12" in pg_monitor_bootstrap
    assert "SUPERUSER" not in pg_monitor_bootstrap
    # PostgreSQL reserves every role name that starts with pg_.
    assert "pg_monitor_user" not in pg_monitor_bootstrap

    lightdash_bootstrap = Path("infra/runtime/postgres/bootstrap/lightdash.sql").read_text(
        encoding="utf-8"
    )
    assert 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' in lightdash_bootstrap


def test_compose_uses_aws_credential_chain_without_static_keys() -> None:
    rendered = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (AIRFLOW_COMPOSE, ARXIV_LENS_COMPOSE, LIGHTDASH_COMPOSE, POSTGRES_COMPOSE)
    )

    assert "AWS_ACCESS_KEY_ID" not in rendered
    assert "AWS_SECRET_ACCESS_KEY" not in rendered
    assert "AWS_PROFILE" not in rendered
    assert "AWS_IDENTITY_DIR" in rendered
    assert "AWS_CONFIG_FILE" in rendered


def test_lightdash_uses_owned_database_storage_and_sdk_credentials() -> None:
    payload = _compose(LIGHTDASH_COMPOSE)
    service = payload["services"]["lightdash"]
    environment = service["environment"]

    assert service["image"] == "${LIGHTDASH_IMAGE:-lightdash:local}"
    assert service["entrypoint"][:2] == ["dumb-init", "--"]
    assert service["command"] == ["node", "dist/index.js"]
    assert service["ports"] == ["${HOST_BIND_ADDRESS:-127.0.0.1}:8081:8080"]
    assert set(service["networks"]) == {"metadata", "runtime", "telemetry"}
    assert payload["networks"]["telemetry"] == {
        "external": True,
        "name": "lakehouse-observability",
    }
    assert payload["networks"]["metadata"]["external"] is True
    assert payload["networks"]["runtime"] is None
    assert environment["PGDATABASE"] == "lightdash"
    assert environment["PGHOST"] == "metadata-postgres"
    assert environment["ATHENA_WAREHOUSE_IAM_ROLE_AUTH"] == "true"
    assert environment["S3_USE_CREDENTIALS_FROM"] == "ini"
    assert environment["SECURE_COOKIES"] == "true"
    assert environment["TRUST_PROXY"] == "true"
    assert "ALLOW_MULTIPLE_ORGS" not in environment
    assert "LIGHTDASH_LOG_LEVEL" not in environment
    assert "S3_FORCE_PATH_STYLE" not in environment
    assert "SCHEDULER_ENABLED" not in environment
    assert environment["LIGHTDASH_LOG_CONSOLE_LEVEL"] == "warn"
    assert "S3_ACCESS_KEY" not in environment
    assert "S3_SECRET_KEY" not in environment
    assert set(payload["secrets"]) == {"lightdash_database_password", "lightdash_secret"}
    assert all("environment" in secret for secret in payload["secrets"].values())
    assert (
        "/usr/local/bin/aws_signing_helper:/usr/local/bin/aws_signing_helper:ro"
        in service["volumes"]
    )
    assert "/api/v1/health" in service["healthcheck"]["test"][-1]
    assert service["deploy"]["resources"]["limits"] == {"cpus": "1.0", "memory": "2G"}


def test_cloudflare_connector_is_pinned_hardened_and_file_secret_driven() -> None:
    payload = _compose(CLOUDFLARE_COMPOSE)
    service = payload["services"]["cloudflare-tunnel"]
    command = service["command"]

    assert service["image"] == "${CLOUDFLARE_IMAGE:?CLOUDFLARE_IMAGE is required}"
    assert service["network_mode"] == "host"
    assert service["user"] == "${LOCAL_UID}:${LOCAL_GID}"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["restart"] == "unless-stopped"
    assert "--token-file" in command
    assert "--token" not in command
    assert "127.0.0.1:20241" in command
    assert command[command.index("--loglevel") + 1] == "warn"
    assert set(payload["secrets"]) == {"cloudflare_tunnel_token"}
    assert "CLOUDFLARE_TUNNEL_TOKEN_FILE" in payload["secrets"]["cloudflare_tunnel_token"]["file"]


def test_airflow_emits_structured_info_while_other_services_start_at_warning() -> None:
    airflow = _compose(AIRFLOW_COMPOSE)
    lens = _compose(ARXIV_LENS_COMPOSE)["services"]["arxiv-lens"]
    postgres = _compose(POSTGRES_COMPOSE)["services"]["metadata-postgres"]

    environment = airflow["x-airflow-common"]["environment"]
    assert environment["AIRFLOW__LOGGING__LOGGING_LEVEL"] == "INFO"
    assert environment["AIRFLOW__LOGGING__JSON_LOGS"] == "true"
    assert not (AIRFLOW_RUNTIME / "airflow_runtime/logging_config.py").exists()
    assert lens["environment"]["LAKEHOUSE_LOG_LEVEL"] == "WARNING"
    assert lens["environment"]["STREAMLIT_LOGGER_LEVEL"] == "warning"
    assert postgres["command"] == ["postgres", "-c", "log_min_messages=warning"]


def test_collector_drops_structured_airflow_info_after_parsing_json_level() -> None:
    collector = yaml.safe_load(
        Path("sysops/signoz/collector/config.yaml").read_text(encoding="utf-8")
    )
    operators = collector["receivers"]["receiver_creator/docker"]["receivers"]["filelog/container"][
        "config"
    ]["operators"]
    airflow_parser = next(
        operator for operator in operators if operator.get("id") == "parse_airflow_json"
    )

    # Receiver-created resource attributes are attached after Stanza operators
    # execute, so the parser uses Airflow's stable JSON envelope as its guard.
    assert airflow_parser["if"].startswith('body matches "^\\\\{')
    assert "timestamp" in airflow_parser["if"]
    assert "level" in airflow_parser["if"]
    assert airflow_parser["parse_to"] == "attributes.airflow"
    assert airflow_parser["severity"]["parse_from"] == "attributes.airflow.level"
    assert collector["processors"]["filter/drop_low_severity"]["logs"]["log_record"] == [
        "severity_number != SEVERITY_NUMBER_UNSPECIFIED and severity_number < SEVERITY_NUMBER_WARN"
    ]


def test_collector_observes_itself_and_scopes_postgres_severity_parsing() -> None:
    collector = yaml.safe_load(
        Path("sysops/signoz/collector/config.yaml").read_text(encoding="utf-8")
    )

    scrape = collector["receivers"]["prometheus/collector"]["config"]["scrape_configs"][0]
    assert scrape["job_name"] == "signoz-collection-agent"
    assert scrape["static_configs"] == [{"targets": ["127.0.0.1:8888"]}]
    assert (
        "prometheus/collector"
        in collector["service"]["pipelines"]["metrics/infrastructure"]["receivers"]
    )

    statements = collector["processors"]["transform/log_normalize"]["log_statements"][0][
        "statements"
    ]
    severity_statements = [
        statement for statement in statements if statement.startswith("set(severity_number,")
    ]
    assert severity_statements
    assert all("^metadata-postgres-" in statement for statement in severity_statements)


def test_airflow_metric_allowlist_keeps_operational_health_signals() -> None:
    airflow = _compose(AIRFLOW_COMPOSE)
    environment = airflow["x-airflow-common"]["environment"]
    allowlist = environment["AIRFLOW__METRICS__METRICS_ALLOW_LIST"]
    collector = yaml.safe_load(
        Path("sysops/signoz/collector/config.yaml").read_text(encoding="utf-8")
    )
    airflow_metric_statements = collector["processors"]["transform/airflow_metrics"][
        "metric_statements"
    ]
    dashboard = Path("sysops/signoz/terraform/dashboards_airflow.tf").read_text(encoding="utf-8")

    for family in (
        "critical_section_duration",
        "dag_processor_heartbeat",
        "tasks\\.(executable|starving)",
        "queued_duration",
        "pool\\.",
    ):
        assert family in allowlist
    assert environment["AIRFLOW__METRICS__LEGACY_NAMES_ON"] == "false"
    assert "^task\\." in allowlist
    assert "^dag\\..+" not in allowlist
    assert "ti_(successes|failures)" not in allowlist
    assert "last_duration" not in allowlist
    assert "last_run\\.seconds_ago" not in allowlist
    assert [statement["context"] for statement in airflow_metric_statements] == ["metric"]
    assert "ExtractPatterns" not in str(airflow_metric_statements)
    assert "airflow.ti_successes" not in dashboard
    assert "airflow.ti_failures" not in dashboard
    assert "service.name = 'airflow' AND airflow.dag_id EXISTS" in dashboard
    assert 'name            = "airflow.dag_id"' in dashboard
    assert 'name            = "airflow.task_id"' in dashboard


def test_container_cpu_panels_normalize_docker_percent_to_logical_cores() -> None:
    components = Path("sysops/signoz/terraform/dashboard_components.tf").read_text(encoding="utf-8")
    dashboard = Path("sysops/signoz/terraform/dashboards_containers.tf").read_text(encoding="utf-8")

    assert components.count('formula           = "A / 100"') == 1
    assert dashboard.count('expression = "A / 100"') == 2
    assert dashboard.count('metric_name       = "container.cpu.utilization"') == 2
    assert dashboard.count("limit = 10000") >= 2


def test_airflow_remote_logging_uses_workload_identity_without_secret_lookup() -> None:
    airflow = _compose(AIRFLOW_COMPOSE)
    environment = airflow["x-airflow-common"]["environment"]
    backend_kwargs = json.loads(environment["AIRFLOW__SECRETS__BACKEND_KWARGS"])

    assert environment["AIRFLOW_CONN_AWS_DEFAULT"] == "aws://"
    assert backend_kwargs["connections_lookup_pattern"] == ("^(slack_api_default|smtp_default)$")


def test_local_runtime_does_not_use_dotenv_files() -> None:
    assert not Path(".env").exists()
    assert not Path(".env.example").exists()

    settings = Path("lakehouse/catalog/src/lakehouse/config/settings.py").read_text(
        encoding="utf-8"
    )
    modal_client = Path("ocr-engine/src/document_ocr/modal.py").read_text(encoding="utf-8")

    assert 'env_file=".env"' not in settings
    assert 'env_file=".env"' not in modal_client


def test_arxiv_lens_receives_only_its_explicit_environment() -> None:
    service = _compose(ARXIV_LENS_COMPOSE)["services"]["arxiv-lens"]

    assert "env_file" not in service
    assert "build" not in service
    assert service["image"] == ("${ARXIV_LENS_IMAGE:-arxiv-lens:local}")
    assert service["user"] == "${LOCAL_UID}:0"
    assert set(service["environment"]) == {
        "LAKEHOUSE_ENVIRONMENT",
        "AWS_CONFIG_FILE",
        "AWS_EC2_METADATA_DISABLED",
        "LAKEHOUSE_LOG_LEVEL",
        "STREAMLIT_LOGGER_LEVEL",
        "OTEL_SERVICE_NAME",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_RESOURCE_ATTRIBUTES",
    }
    assert service["environment"]["OTEL_SERVICE_NAME"] == "arxiv-lens"
    assert service["environment"]["OTEL_EXPORTER_OTLP_PROTOCOL"] == "grpc"
    assert service["environment"]["OTEL_METRICS_EXPORTER"] == "none"
    assert service["environment"]["OTEL_LOGS_EXPORTER"] == "none"
    assert service["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "http://signoz-collection-agent:4317"
    )
    assert "extra_hosts" not in service
    assert _compose(ARXIV_LENS_COMPOSE)["networks"]["telemetry"] == {
        "external": True,
        "name": "lakehouse-observability",
    }
    assert service["volumes"] == ["${AWS_IDENTITY_DIR}/arxiv-lens:/run/aws:ro"]


def test_all_container_images_are_immutable() -> None:
    airflow_dockerfile = (AIRFLOW_RUNTIME / "Dockerfile").read_text(encoding="utf-8")
    dbt_dockerfile = Path("analytics/dbt-project/Dockerfile").read_text(encoding="utf-8")
    lens_dockerfile = Path("arxiv-lens/Dockerfile").read_text(encoding="utf-8")
    emr_dockerfile = Path("lakehouse/emr/Dockerfile").read_text(encoding="utf-8")
    compose = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (AIRFLOW_COMPOSE, POSTGRES_COMPOSE, CLOUDFLARE_COMPOSE)
    )

    assert not Path("Dockerfile").exists()
    assert "apache/airflow:3.3.0-python3.12@sha256:" in airflow_dockerfile
    assert "automation/airflow/uv.lock" in airflow_dockerfile
    assert "COPY --chown=airflow:0 automation/airflow/bundle" not in airflow_dockerfile
    assert '"/uv", "export", "--frozen", "--no-dev"' in airflow_dockerfile
    assert "uv pip sync --python /home/airflow/.local/bin/python" in airflow_dockerfile
    assert "USER airflow" in airflow_dockerfile
    assert 'ENTRYPOINT ["dbt"]' in dbt_dockerfile
    assert "analytics/dbt-project/runtime/uv.lock" in dbt_dockerfile
    assert "COPY analytics/dbt-project/models ./models" in dbt_dockerfile
    assert "dbt deps" in dbt_dockerfile
    assert "USER dbt" in dbt_dockerfile
    assert "USER lens" in lens_dockerfile
    assert "HEALTHCHECK" in lens_dockerfile
    assert "PYTHONPATH=/app/arxiv-lens/src" in lens_dockerfile
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert "!infra/runtime/identity/install-aws-signing-helper" in dockerignore
    assert '"apache-airflow[postgres]==3.3.0"' in (AIRFLOW_PROJECT / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "postgres:17.10@sha256:" in compose
    assert "public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:" in emr_dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.30@sha256:" in emr_dockerfile
    dockerfiles = "\n".join((airflow_dockerfile, dbt_dockerfile, lens_dockerfile, emr_dockerfile))
    assert dockerfiles.count("ghcr.io/astral-sh/uv:0.11.30@sha256:") == 4
    assert ":latest" not in f"{dockerfiles}\n{compose}"


def test_python_dependencies_are_owned_by_their_runtime_domain() -> None:
    workspace = Path("pyproject.toml").read_text(encoding="utf-8")
    catalog = Path("lakehouse/catalog/pyproject.toml").read_text(encoding="utf-8")
    orchestration = (AIRFLOW_PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    lens = Path("arxiv-lens/pyproject.toml").read_text(encoding="utf-8")
    analytics = Path("analytics/dbt-project/runtime/pyproject.toml").read_text(encoding="utf-8")
    ocr = Path("ocr-engine/pyproject.toml").read_text(encoding="utf-8")

    assert "apache-airflow" not in workspace
    assert "dbt-athena" not in workspace
    assert "streamlit" not in workspace
    assert 'name = "lakehouse"' in catalog
    assert '"apache-airflow[postgres]==3.3.0"' in orchestration
    assert '"apache-airflow-providers-amazon==9.32.0"' in orchestration
    assert "aiobotocore" not in orchestration
    assert '"apache-airflow-providers-docker==4.5.7"' in orchestration
    assert '"apache-airflow-providers-git==0.4.1"' in orchestration
    assert '"apache-airflow-providers-smtp==3.0.1"' in orchestration
    assert '"psycopg2-binary' not in orchestration
    assert "constraint-dependencies" not in orchestration
    assert "document-ocr" not in orchestration
    assert '"lakehouse"' not in orchestration
    assert '"streamlit>=1.60,<1.61"' in lens
    for dependency in (
        "awswrangler",
        "boto3",
        "document-ocr",
        "lakehouse",
        "loguru",
        "pandas",
        "pydantic",
        "streamlit",
    ):
        assert dependency in lens
    assert "cli = [" in ocr
    assert "providers = [" not in ocr
    assert "s3fs" not in ocr
    assert '"dbt-athena==1.11.0"' in analytics
    assert 'members = ["arxiv-lens", "lakehouse/catalog", "ocr-engine"]' in workspace
    assert (
        'exclude = ["analytics/dbt-project/runtime", "automation/airflow", "lakehouse/emr", '
        '"ocr-engine/modal"]' in workspace
    )
    assert Path("analytics/dbt-project/runtime/uv.lock").is_file()
    assert (AIRFLOW_PROJECT / "uv.lock").is_file()
    assert "apache-airflow" not in Path("uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry-distro" in lens
    assert "apache-airflow" in (AIRFLOW_PROJECT / "uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry" in (AIRFLOW_PROJECT / "uv.lock").read_text(encoding="utf-8")


def test_airflow_bundle_and_runtime_have_one_way_ownership() -> None:
    bundle = AIRFLOW_PROJECT / "bundle"
    runtime = AIRFLOW_PROJECT / "runtime"

    assert (bundle / "dags").is_dir()
    assert (bundle / ".airflowignore").read_text(encoding="utf-8").strip() == "tests/"
    assert (bundle / "operators").is_dir()
    assert (bundle / "callbacks").is_dir()
    assert (bundle / "config").is_dir()
    assert not (bundle / "airflow_bundle").exists()
    assert (runtime / "airflow_runtime/secrets.py").is_file()
    assert (AIRFLOW_PROJECT / "deploy/compose.yaml").is_file()
    assert Path(POSTGRES_COMPOSE).is_file()
    assert not (bundle / "config/aws_secrets.py").exists()

    bundle_source = "\n".join(path.read_text(encoding="utf-8") for path in bundle.rglob("*.py"))
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (runtime / "airflow_runtime").rglob("*.py")
    )
    assert "from automation" not in bundle_source
    assert "airflow_runtime" not in bundle_source
    assert "from callbacks" not in runtime_source
    assert "from config" not in runtime_source
    assert "from operators" not in runtime_source


def test_lakehouse_data_plane_has_explicit_capability_boundaries() -> None:
    assert Path("lakehouse/catalog/pyproject.toml").is_file()
    assert Path("lakehouse/contracts").is_dir()
    assert Path("lakehouse/catalog/src/lakehouse/catalog").is_dir()
    assert Path("lakehouse/emr/pyproject.toml").is_file()
    assert not Path("src").exists()
    assert not Path("contracts").exists()
    assert not Path("platform").exists()
    assert not Path("jobs").exists()

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("lakehouse/catalog/src/lakehouse").rglob("*.py")
    )
    assert "lakehouse.platform" not in sources
