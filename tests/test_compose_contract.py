import json
from pathlib import Path
from typing import Any

import yaml

AIRFLOW_COMPOSE = "orchestration/deploy/compose.yaml"
INSPECTOR_COMPOSE = "apps/arxiv_inspector/deploy/compose.yaml"
LIGHTDASH_COMPOSE = "apps/lightdash/deploy/compose.yaml"
POSTGRES_COMPOSE = "infra/runtime/postgres/compose.yaml"
CLOUDFLARE_COMPOSE = "infra/runtime/cloudflare/compose.yaml"
AIRFLOW_PROJECT = Path("orchestration")
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
    assert set(_compose(INSPECTOR_COMPOSE)["services"]) == {"arxiv-inspector"}
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
            "name": "lakehouse",
            "classpath": "airflow.providers.git.bundles.git.GitDagBundle",
            "kwargs": {
                "repo_url": "https://github.com/ToGiaBaoKDL/mini-lakehouse.git",
                "tracking_ref": "main",
                "subdir": "orchestration/bundle",
                "sparse_dirs": ["orchestration/bundle"],
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
    assert "AIRFLOW_CONN_AWS_DEFAULT" not in environment
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
    airflow_secrets = Path("orchestration/deploy/initialize-secrets").read_text(encoding="utf-8")
    postgres_secrets = Path("infra/runtime/postgres/initialize-secrets").read_text(encoding="utf-8")
    airflow_reconcile = Path("orchestration/deploy/reconcile").read_text(encoding="utf-8")
    assert '"version":1' in airflow_secrets + postgres_secrets
    assert ".version == 1" in airflow_secrets + postgres_secrets
    assert "admin_password" in airflow_secrets
    assert "AIRFLOW_ADMIN_PASSWORDS" in airflow_reconcile
    assert "infra/runtime/postgres/initialize-secrets" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets bootstrap" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets airflow" in services_makefile
    assert "infra/runtime/postgres/initialize-secrets lightdash" in services_makefile
    assert "orchestration/deploy/initialize-secrets" in services_makefile
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
        for path in (AIRFLOW_COMPOSE, INSPECTOR_COMPOSE, LIGHTDASH_COMPOSE, POSTGRES_COMPOSE)
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
    inspector = _compose(INSPECTOR_COMPOSE)["services"]["arxiv-inspector"]
    postgres = _compose(POSTGRES_COMPOSE)["services"]["metadata-postgres"]

    environment = airflow["x-airflow-common"]["environment"]
    assert environment["AIRFLOW__LOGGING__LOGGING_LEVEL"] == "INFO"
    assert environment["AIRFLOW__LOGGING__JSON_LOGS"] == "true"
    assert not (AIRFLOW_RUNTIME / "airflow_runtime/logging_config.py").exists()
    assert inspector["environment"]["LAKEHOUSE_LOG_LEVEL"] == "WARNING"
    assert inspector["environment"]["STREAMLIT_LOGGER_LEVEL"] == "warning"
    assert postgres["command"] == ["postgres", "-c", "log_min_messages=warning"]


def test_collector_drops_structured_airflow_info_after_parsing_json_level() -> None:
    collector = yaml.safe_load(
        Path("observability/signoz/collector/config.yaml").read_text(encoding="utf-8")
    )
    operators = collector["receivers"]["receiver_creator/docker"]["receivers"]["filelog/container"][
        "config"
    ]["operators"]
    airflow_parser = next(
        operator for operator in operators if operator.get("id") == "parse_airflow_json"
    )

    assert airflow_parser["if"] == 'resource["container.name"] matches "(?i)^airflow-"'
    assert airflow_parser["parse_to"] == "attributes.airflow"
    assert airflow_parser["severity"]["parse_from"] == "attributes.airflow.level"
    assert collector["processors"]["filter/drop_low_severity"]["logs"]["log_record"] == [
        "severity_number != SEVERITY_NUMBER_UNSPECIFIED and severity_number < SEVERITY_NUMBER_WARN"
    ]


def test_local_runtime_does_not_use_dotenv_files() -> None:
    assert not Path(".env").exists()
    assert not Path(".env.example").exists()

    settings = Path("platform/src/lakehouse/config/settings.py").read_text(encoding="utf-8")
    ocr_settings = Path("ocr/src/document_ocr/settings.py").read_text(encoding="utf-8")

    assert 'env_file=".env"' not in settings
    assert 'env_file=".env"' not in ocr_settings


def test_arxiv_inspector_receives_only_its_explicit_environment() -> None:
    service = _compose(INSPECTOR_COMPOSE)["services"]["arxiv-inspector"]

    assert "env_file" not in service
    assert "build" not in service
    assert service["image"] == ("${ARXIV_INSPECTOR_IMAGE:-arxiv-inspector:local}")
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
    assert service["environment"]["OTEL_SERVICE_NAME"] == "arxiv-inspector"
    assert service["environment"]["OTEL_EXPORTER_OTLP_PROTOCOL"] == "grpc"
    assert service["environment"]["OTEL_METRICS_EXPORTER"] == "none"
    assert service["environment"]["OTEL_LOGS_EXPORTER"] == "none"
    assert service["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "http://signoz-collection-agent:4317"
    )
    assert "extra_hosts" not in service
    assert _compose(INSPECTOR_COMPOSE)["networks"]["telemetry"] == {
        "external": True,
        "name": "lakehouse-observability",
    }
    assert service["volumes"] == ["${AWS_IDENTITY_DIR}/arxiv-inspector:/run/aws:ro"]


def test_all_container_images_are_immutable() -> None:
    airflow_dockerfile = (AIRFLOW_RUNTIME / "Dockerfile").read_text(encoding="utf-8")
    dbt_dockerfile = Path("dbt/Dockerfile").read_text(encoding="utf-8")
    inspector_dockerfile = Path("apps/arxiv_inspector/Dockerfile").read_text(encoding="utf-8")
    ocr_dockerfile = Path("ocr/Dockerfile").read_text(encoding="utf-8")
    emr_dockerfile = Path("jobs/emr/Dockerfile").read_text(encoding="utf-8")
    compose = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (AIRFLOW_COMPOSE, POSTGRES_COMPOSE, CLOUDFLARE_COMPOSE)
    )

    assert not Path("Dockerfile").exists()
    assert "apache/airflow:3.3.0-python3.12@sha256:" in airflow_dockerfile
    assert "orchestration/uv.lock" in airflow_dockerfile
    assert "COPY --chown=airflow:0 orchestration/bundle" not in airflow_dockerfile
    assert '"/uv", "export", "--frozen", "--no-dev"' in airflow_dockerfile
    assert "uv pip sync --python /home/airflow/.local/bin/python" in airflow_dockerfile
    assert "USER airflow" in airflow_dockerfile
    assert 'ENTRYPOINT ["dbt"]' in dbt_dockerfile
    assert "dbt/runtime/uv.lock" in dbt_dockerfile
    assert "COPY dbt/models ./models" in dbt_dockerfile
    assert "dbt deps" in dbt_dockerfile
    assert "USER dbt" in dbt_dockerfile
    assert 'ENTRYPOINT ["document-ocr"]' in ocr_dockerfile
    assert "openjdk-21-jre-headless" in ocr_dockerfile
    assert "&& java -version" in ocr_dockerfile
    assert "USER worker" in ocr_dockerfile
    assert "USER inspector" in inspector_dockerfile
    assert "HEALTHCHECK" in inspector_dockerfile
    assert "PYTHONPATH=/app" in inspector_dockerfile
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert "!infra/runtime/identity/install-aws-signing-helper" in dockerignore
    assert '"apache-airflow[postgres]==3.3.0"' in (AIRFLOW_PROJECT / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "postgres:17.10@sha256:" in compose
    assert "public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:" in emr_dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.30@sha256:" in emr_dockerfile
    dockerfiles = "\n".join(
        (airflow_dockerfile, dbt_dockerfile, inspector_dockerfile, ocr_dockerfile, emr_dockerfile)
    )
    assert dockerfiles.count("ghcr.io/astral-sh/uv:0.11.30@sha256:") == 5
    assert ":latest" not in f"{dockerfiles}\n{compose}"


def test_python_dependencies_are_owned_by_their_runtime_domain() -> None:
    workspace = Path("pyproject.toml").read_text(encoding="utf-8")
    platform = Path("platform/pyproject.toml").read_text(encoding="utf-8")
    orchestration = (AIRFLOW_PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    inspector = Path("apps/arxiv_inspector/pyproject.toml").read_text(encoding="utf-8")
    analytics = Path("dbt/runtime/pyproject.toml").read_text(encoding="utf-8")
    ocr = Path("ocr/pyproject.toml").read_text(encoding="utf-8")

    assert "apache-airflow" not in workspace
    assert "dbt-athena" not in workspace
    assert "streamlit" not in workspace
    assert 'name = "lakehouse"' in platform
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
    assert '"streamlit>=1.60,<1.61"' in inspector
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
        assert dependency in inspector
    assert "worker = [" in ocr
    assert "providers = [" not in ocr
    assert "s3fs" not in ocr
    assert '"dbt-athena==1.11.0"' in analytics
    assert 'members = ["apps/arxiv_inspector", "ocr", "platform"]' in workspace
    assert (
        'exclude = ["dbt/runtime", "jobs/emr", "ocr/runners/glm_ocr", "orchestration"]' in workspace
    )
    assert Path("dbt/runtime/uv.lock").is_file()
    assert (AIRFLOW_PROJECT / "uv.lock").is_file()
    assert "apache-airflow" not in Path("uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry-distro" in inspector
    assert "apache-airflow" in (AIRFLOW_PROJECT / "uv.lock").read_text(encoding="utf-8")
    assert "opentelemetry" in (AIRFLOW_PROJECT / "uv.lock").read_text(encoding="utf-8")


def test_airflow_bundle_and_runtime_have_one_way_ownership() -> None:
    bundle = Path("orchestration/bundle")
    runtime = Path("orchestration/runtime")

    assert (bundle / "dags").is_dir()
    assert (bundle / ".airflowignore").read_text(encoding="utf-8").strip() == "tests/"
    assert (bundle / "operators").is_dir()
    assert (bundle / "callbacks").is_dir()
    assert (bundle / "config").is_dir()
    assert not (bundle / "airflow_bundle").exists()
    assert (runtime / "airflow_runtime/secrets.py").is_file()
    assert (Path("orchestration/deploy") / "compose.yaml").is_file()
    assert Path(POSTGRES_COMPOSE).is_file()
    assert not (bundle / "config/aws_secrets.py").exists()

    bundle_source = "\n".join(path.read_text(encoding="utf-8") for path in bundle.rglob("*.py"))
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (runtime / "airflow_runtime").rglob("*.py")
    )
    assert "from orchestration" not in bundle_source
    assert "airflow_runtime" not in bundle_source
    assert "from callbacks" not in runtime_source
    assert "from config" not in runtime_source
    assert "from operators" not in runtime_source


def test_platform_control_plane_is_one_owned_workspace_package() -> None:
    assert Path("platform/pyproject.toml").is_file()
    assert Path("platform/contracts").is_dir()
    assert Path("platform/src/lakehouse/catalog").is_dir()
    assert not Path("src").exists()
    assert not Path("contracts").exists()

    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("platform/src/lakehouse").rglob("*.py")
    )
    assert "lakehouse.platform" not in sources
