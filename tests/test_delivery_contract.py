import json
import re
from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_external_github_actions_are_pinned_by_commit() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path(".github").rglob("*.yml"))
    external_uses = re.findall(r"uses:\s+([^\s]+)", source)

    for action in external_uses:
        if action.startswith("./"):
            continue
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action

    workflows = "\n".join(path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml"))
    assert workflows.count("actions/checkout@") == workflows.count("persist-credentials: false")


def test_signoz_validates_on_changes_but_deploys_only_on_demand() -> None:
    source = _workflow("deploy-signoz.yml")
    triggers = source[source.index("on:\n") : source.index("\njobs:\n")]

    assert "pull_request:" in triggers
    assert "push:" in triggers
    assert "workflow_dispatch:" in triggers
    assert "github.event_name == 'workflow_dispatch'" in source


def test_component_release_publishes_before_protected_digest_deployment() -> None:
    release = _workflow("_release-image.yml")
    action = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
    host_action = Path(".github/actions/reconcile-services-host/action.yml").read_text(
        encoding="utf-8"
    )
    script = Path("infra/runtime/delivery/deploy-component").read_text(encoding="utf-8")

    assert "environment: dev" in release
    assert release.index("  publish:\n") < release.index("  deploy:\n")
    assert "needs: publish" in release
    assert "image: ${{ needs.publish.outputs.image }}" in release
    publish_job = release[release.index("  publish:\n") : release.index("  deploy:\n")]
    deploy_job = release[release.index("  deploy:\n") :]
    assert "environment:" not in publish_job
    assert "queue: max" in publish_job
    assert "queue: max" not in deploy_job
    assert "dev-${{ inputs.component }}-publish" in publish_job
    assert "dev-${{ inputs.component }}-deploy" in deploy_job
    assert "id-token: write" in release
    assert "github.ref == 'refs/heads/main'" in release
    assert "default: linux/amd64,linux/arm64" in release
    assert "platforms: ${{ inputs.platforms }}" in release
    assert "provenance: mode=max" in release
    assert "sbom: true" in release
    assert "imageTag=$GITHUB_SHA" in release
    assert "aws ecr batch-get-image" in release
    assert "aws ecr put-image" in release
    assert "Resolve immutable image source revision" in release
    assert "automation/airflow/pyproject.toml" in release
    assert "automation/airflow/uv.lock" in release
    assert "automation/airflow/runtime" in release
    assert "source-$revision" in release
    assert "cancel-in-progress: false" in release
    assert "airflow|arxiv-lens|dbt|lakehouse-ingest|lightdash|t0-trading" in release
    assert '[[ -z "$BUILD_ARGS" ]]' in release
    assert "^[0-9]{12}\\.dkr\\.ecr\\." in action
    assert "uses: ./.github/actions/reconcile-services-host" in action
    assert "tag:tgbao-dev-ci" in host_action
    assert "tailscale ssh ubuntu@tgbao-dev-services" in host_action
    assert "deployment/release_manifest" not in release + action + script
    assert "latest" not in release + action + script
    assert 'tar -C "$SOURCE_ROOT" -cf -' in action
    assert "bundle+=(t0-trading/deploy)" in action
    assert "source_root:" in action
    assert "revision:" not in action
    assert "git init" not in script
    assert "git fetch" not in script
    assert "make -C" not in script
    assert "HOST_BIND_ADDRESS=${HOST_BIND_ADDRESS:-0.0.0.0}" in script
    assert "AIRFLOW_BASE_URL=${AIRFLOW_BASE_URL:-https://airflow.tgblab.io.vn}" in script


def test_service_pull_uses_short_lived_registry_login() -> None:
    pull = Path("infra/runtime/delivery/pull-image").read_text(encoding="utf-8")

    assert "aws ecr get-login-password" in pull
    assert "docker logout" in pull
    assert 'DOCKER_CONFIG="$docker_config"' in pull
    assert "mktemp -d" in pull
    assert "environment=${LAKEHOUSE_ENVIRONMENT:-dev}" in pull
    assert 'repository=$("$script_dir/image-repository" "$component")' in pull
    assert "/$repository@sha256" in pull
    assert "Image must be an immutable digest from the $repository" in pull
    assert "services-deployer/host-config" in pull
    assert "AWS CLI v2 is missing from the services host." in pull


def test_image_repositories_follow_capability_ownership() -> None:
    repository = Path("infra/runtime/delivery/image-repository").read_text(encoding="utf-8")
    release = _workflow("_release-image.yml")
    deploy = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
    rollback = _workflow("rollback-component.yml")

    for mapping in (
        "airflow) capability=automation-airflow",
        "arxiv-lens) capability=arxiv-lens",
        "dbt) capability=analytics-dbt",
        "lightdash) capability=analytics-lightdash",
        "lakehouse-ingest) capability=lakehouse-ingest",
        "t0-trading) capability=t0-trading",
    ):
        assert mapping in repository
    assert "printf '%s\\n' \"tgbao-$environment-$capability\"" in repository
    assert 'expected_repository=$(infra/runtime/delivery/image-repository "$COMPONENT")' in release
    assert '"$SOURCE_ROOT/infra/runtime/delivery/image-repository"' in deploy
    assert 'repository=$(infra/runtime/delivery/image-repository "$COMPONENT")' in rollback


def test_host_workload_identities_ignore_operator_credentials() -> None:
    sources = [
        Path(path).read_text(encoding="utf-8")
        for path in (
            "infra/runtime/cloudflare/deploy",
            "infra/runtime/delivery/pull-image",
            "infra/runtime/postgres/backup",
            "infra/runtime/postgres/deploy",
            "infra/runtime/postgres/restore",
            "automation/airflow/deploy/reconcile",
            "analytics/lightdash/deploy/reconcile",
        )
    ]

    assert all("AWS_SHARED_CREDENTIALS_FILE=/dev/null" in source for source in sources)


def test_modal_worker_keeps_models_cached_before_application_source() -> None:
    source = Path("ocr-engine/modal/app.py").read_text(encoding="utf-8")

    assert source.index(".run_function(") < source.index(".env(") < source.index(".add_local_dir(")
    assert "def download_models(" in source
    assert "snapshot_download(" in source
    assert "run_commands" not in source
    assert '.env({"PYTHONPATH": str(MODAL_ROOT)})' in source
    assert '.add_local_file("ocr-engine/config.yaml", "/root/ocr-engine/config.yaml")' in source
    assert "@app.cls(" in source
    assert "@modal.enter()" in source
    assert "@modal.exit()" in source
    assert "@modal.method()" in source


def test_ocr_is_local_cli_plus_modal_without_an_oci_runtime() -> None:
    makefile = Path("make/data.mk").read_text(encoding="utf-8")

    assert not Path("ocr-engine/Dockerfile").exists()
    assert not Path("ocr-engine/deploy").exists()
    assert not Path(".github/workflows/release-ocr-worker.yml").exists()
    assert not Path(
        "automation/airflow/bundle/dags/arxiv/etl_docker_arxiv_document_ocr.py"
    ).exists()
    assert "ocr-run: preflight" not in makefile
    assert "document-ocr run" in makefile
    assert "modal deploy" in makefile


def test_t0_certification_uses_the_official_read_only_sdk_boundary() -> None:
    project = Path("t0-trading/pyproject.toml").read_text(encoding="utf-8")
    certification = Path("t0-trading/src/t0_trading/certification.py").read_text(encoding="utf-8")
    capture = Path("t0-trading/src/t0_trading/capture/rest.py").read_text(encoding="utf-8")
    cli = Path("t0-trading/src/t0_trading/cli.py").read_text(encoding="utf-8")
    credentials = Path("t0-trading/src/t0_trading/credentials.py").read_text(encoding="utf-8")
    provider = Path("t0-trading/src/t0_trading/provider.py").read_text(encoding="utf-8")

    assert '"ssi-sdk==3.2.1"' in project
    assert "from ssi_sdk import Data, Stream" in certification
    assert "from ssi_sdk import Data, Stream" in cli
    assert "from ssi_sdk import Auth, Config" in provider
    assert "market.get_ohlc_1minute_historical" in capture
    assert "market.get_master_data_historical" in capture
    assert "SSI_SDK_VERSION" in certification
    assert "importlib.metadata" not in certification
    assert "auth.authenticate()" in provider
    assert "auth.token_manager" not in certification
    assert "get_master_data_historical" in certification
    assert "get_ohlc_1minute" in certification
    assert "get_securities_summary_by_index" in certification
    assert "subscribe_symbol" in certification
    assert "subscribe_symbol_ohlcv" in certification
    assert "subscribe_index" in certification
    assert "client.ping()" in certification
    assert "client.ping()" in Path("t0-trading/src/t0_trading/capture/stream.py").read_text(
        encoding="utf-8"
    )
    assert "Trading" not in certification
    assert "private_key=" not in certification
    assert 'boto3.client("secretsmanager"' in credentials
    capability = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("t0-trading/src/t0_trading").rglob("*.py")
    )
    for direct_client in ("import httpx", "import requests", "import websockets"):
        assert direct_client not in capability
    for protocol_literal in (
        "fc-data.ssi.com.vn",
        "fc-datahub.ssi.com.vn",
        "B:VIC",
        "X-QUOTE:VIC",
        "MI:ALL",
    ):
        assert protocol_literal not in capability


def test_t0_market_core_is_side_effect_free() -> None:
    market = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("t0-trading/src/t0_trading/market").glob("*.py")
    )

    for dependency in ("boto3", "botocore", "sqlalchemy", "psycopg"):
        assert dependency not in market


def test_each_component_owns_its_deployment_operation() -> None:
    airflow = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "automation/airflow/deploy/deploy",
            "automation/airflow/deploy/reconcile",
        )
    )
    lens = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "arxiv-lens/deploy/deploy",
            "arxiv-lens/deploy/reconcile",
        )
    )
    dbt = Path("analytics/dbt-project/deploy/deploy").read_text(encoding="utf-8")
    postgres = Path("infra/runtime/postgres/deploy").read_text(encoding="utf-8")
    lightdash = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "analytics/lightdash/deploy/deploy",
            "analytics/lightdash/deploy/reconcile",
        )
    )
    cloudflare = Path("infra/runtime/cloudflare/deploy").read_text(encoding="utf-8")
    t0_trading = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("t0-trading/deploy/deploy", "t0-trading/deploy/reconcile")
    )

    assert "docker compose --project-name airflow" in airflow
    assert "--force-recreate" not in airflow
    assert "--remove-orphans" in airflow
    assert "airflow/remote_log_uri" in airflow
    assert "infra/runtime/postgres/deploy" in airflow
    assert "compose logs --no-color --tail 200 airflow-volumes-init airflow-init" in airflow
    assert "docker compose --project-name metadata-postgres" in postgres
    assert '"$bundle_root/infra/runtime/postgres/deploy" airflow' in airflow
    assert "docker compose --project-name lightdash" in lightdash
    assert '"$bundle_root/infra/runtime/postgres/deploy" lightdash' in lightdash
    assert "infra/runtime/postgres/deploy" not in t0_trading
    assert "docker compose --project-name t0-trading" in t0_trading
    assert "create --force-recreate --remove-orphans" in t0_trading
    assert 'sudo "$script_dir/reconcile-schedule"' in t0_trading
    assert 'if "$script_dir/stream-window"; then' in t0_trading
    assert "did not become healthy within 120 seconds" in t0_trading
    assert "storage/landing_uri" in t0_trading
    assert "T0_STREAM_SPOOL_DIR" in t0_trading
    assert "install -d -m 0700" in t0_trading
    assert "--force-recreate" not in lightdash
    assert "docker compose --project-name arxiv-lens" in lens
    assert '"$1" dbt:runtime' in dbt
    assert "deploy/deploy <image@sha256:digest>" in dbt
    assert "docker compose --project-name cloudflare" in cloudflare
    assert "--token-file" in Path("infra/runtime/cloudflare/compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "git " not in airflow + lens + lightdash + dbt + postgres + cloudflare + t0_trading


def test_t0_stream_schedule_is_component_owned_and_fail_safe() -> None:
    deploy = Path("t0-trading/deploy")
    reconcile = (deploy / "reconcile-schedule").read_text(encoding="utf-8")
    window = (deploy / "stream-window").read_text(encoding="utf-8")
    systemd = deploy / "systemd"
    capture = (systemd / "lakehouse-t0-stream-capture.service").read_text(encoding="utf-8")
    start = (systemd / "lakehouse-t0-stream-start.timer").read_text(encoding="utf-8")
    stop = (systemd / "lakehouse-t0-stream-stop.timer").read_text(encoding="utf-8")
    stop_service = (systemd / "lakehouse-t0-stream-stop.service").read_text(encoding="utf-8")

    assert "flock 9" in reconcile
    assert "systemctl daemon-reload" in reconcile
    assert "systemctl enable --now" in reconcile
    assert "/usr/local/sbin/lakehouse-t0-stream-window" in reconcile + capture
    assert "TZ=Asia/Ho_Chi_Minh date +%u:%H" in window
    assert "ExecStart=/usr/bin/docker start --attach lakehouse-t0-stream-capture" in capture
    assert "ExecStop=-/usr/bin/docker stop --time 30 lakehouse-t0-stream-capture" in capture
    assert "Restart=on-failure" in capture
    assert "WantedBy=multi-user.target" not in capture
    assert "OnCalendar=Mon..Fri *-*-* 08:00:00 Asia/Ho_Chi_Minh" in start
    assert "OnCalendar=Mon..Fri *-*-* 16:00:00 Asia/Ho_Chi_Minh" in stop
    assert "Persistent=true" in start + stop
    assert "Conflicts=lakehouse-t0-stream-capture.service" in stop_service


def test_top_level_arxiv_lens_resolves_the_release_bundle_root() -> None:
    deploy = Path("arxiv-lens/deploy/deploy").read_text(encoding="utf-8")

    assert '"$script_dir/../.."' in deploy
    assert '"$script_dir/../../.."' not in deploy


def test_cloudflare_connector_has_a_deploy_only_workflow() -> None:
    source = _workflow("deploy-cloudflare.yml")
    action = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
    sync = Path("infra/runtime/cloudflare/sync-secret").read_text(encoding="utf-8")
    image = Path("infra/runtime/cloudflare/image").read_text(encoding="utf-8").strip()

    assert "workflow_dispatch:" in source
    assert "environment: dev" in source
    assert "component: cloudflare" in source
    assert image == (
        "cloudflare/cloudflared:2026.7.3@sha256:"
        "e39ee8da81ad5e05d77f38d2f51c60ca51bf2a8450ac3abab50c17fdb91d91bf"
    )
    assert "image=$(<infra/runtime/cloudflare/image)" in source
    assert "${{ steps.release.outputs.image }}" in source
    assert "docker/build-push-action" not in source
    assert "amazon-ecr-login" not in source
    assert ".github/actions/deploy-component/action.yml" not in source
    assert "infra/runtime/cloudflare" in action
    assert "file:///dev/stdin" in sync
    assert "CLOUDFLARE_API_TOKEN" in sync
    assert 'secret-string "$token"' not in sync


def test_services_host_logging_is_reconciled_independently_of_image_builds() -> None:
    workflow = _workflow("deploy-services-host.yml")
    action = Path(".github/actions/reconcile-services-host/action.yml").read_text(encoding="utf-8")
    reconcile = Path("infra/runtime/host/reconcile-docker-logging").read_text(encoding="utf-8")

    assert "environment: dev" in workflow
    assert "uses: ./.github/actions/reconcile-services-host" in workflow
    assert "infra/runtime/host/docker-daemon.json" in workflow + action
    assert "docker/build-push-action" not in workflow + action
    assert "dockerd --validate" in reconcile
    assert "flock 9" in reconcile
    assert 'cmp -s "$source_config" "$target_config"' in reconcile
    assert "systemctl restart docker" in reconcile


def test_services_host_owns_the_shared_observability_network() -> None:
    workflow = _workflow("deploy-services-host.yml")
    action = Path(".github/actions/reconcile-services-host/action.yml").read_text(encoding="utf-8")
    reconcile = Path("infra/runtime/host/reconcile-docker-networks").read_text(encoding="utf-8")

    assert "infra/runtime/host/reconcile-docker-networks" in workflow + action
    assert "lakehouse-observability" in reconcile
    assert "docker network inspect" in reconcile
    assert "docker network create" in reconcile
    assert "subnet=172.24.0.0/16" in reconcile
    assert "gateway=172.24.0.1" in reconcile
    assert "unexpected IPAM" in reconcile
    assert "flock 9" in reconcile


def test_workload_identity_install_reconciles_the_host_desired_state() -> None:
    makefile = Path("make/infra.mk").read_text(encoding="utf-8")
    renderer = Path("infra/runtime/identity/workload-identities").read_text(encoding="utf-8")
    target = makefile[makefile.index("workload-identities-install:") :]

    assert "workload-identities-install: workload-identities-render" in makefile
    assert 'desired_workloads="$(printf' in renderer
    assert "Refusing to remove unmanaged identity directory: $bundle" in renderer
    assert 'rm -rf "$bundle"' in renderer
    assert "Removed stale workload identity: $workload" in renderer
    assert 'staged="$$(mktemp -d "$$parent/.aws.XXXXXX")"' in target
    assert 'temporary="$$(mktemp "$$destination/.$$file.XXXXXX")"' in target
    assert 'mv "$$temporary" "$$destination/$$file"' in target
    assert "Refusing to remove unmanaged host identity: $$destination" in target
    assert "Removed stale host workload identity: $$workload" in target
    assert "trap cleanup EXIT; trap 'exit 1' HUP INT TERM" in target
    assert 'mv "$$target" "$$previous"' not in target


def test_metadata_backup_schedule_is_reconciled_from_reviewed_repo_sources() -> None:
    workflow = _workflow("deploy-services-host.yml")
    action = Path(".github/actions/reconcile-services-host/action.yml").read_text(encoding="utf-8")
    reconcile = Path("infra/runtime/host/reconcile-metadata-backup").read_text(encoding="utf-8")
    backup = Path("infra/runtime/postgres/backup").read_text(encoding="utf-8")
    restore = Path("infra/runtime/postgres/restore").read_text(encoding="utf-8")
    service = Path("infra/runtime/host/systemd/lakehouse-metadata-backup.service").read_text(
        encoding="utf-8"
    )
    timer = Path("infra/runtime/host/systemd/lakehouse-metadata-backup.timer").read_text(
        encoding="utf-8"
    )

    assert "infra/runtime/postgres/backup" in workflow + action
    assert "infra/runtime/host/reconcile-metadata-backup" in workflow
    assert "infra/runtime/host/systemd/lakehouse-metadata-backup.service" in workflow
    assert "infra/runtime/host/systemd/lakehouse-metadata-backup.timer" in workflow
    assert "docker/build-push-action" not in action

    assert "flock 9" in reconcile
    assert 'install -m 0755 "$source_backup" "$target_backup"' in reconcile
    assert "systemctl enable --now lakehouse-metadata-backup.timer" in reconcile
    assert "systemctl daemon-reload" in reconcile

    assert "/usr/local/sbin/lakehouse-metadata-backup" in service + reconcile
    assert "Type=oneshot" in service
    assert "User=ubuntu" in service
    assert "OnCalendar=*-*-* 02,14:30:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=lakehouse-metadata-backup.service" in timer

    assert "backup/metadata_postgres_uri" in backup
    assert "AWS_SHARED_CREDENTIALS_FILE=/dev/null" in backup
    assert 's3api head-object --bucket "$bucket_name" --key "$checksum_key"' in backup
    assert "--if-none-match" not in backup
    assert "pg_dump --username postgres --format=custom --no-password" in backup
    assert "slot=am" in backup
    assert "slot=pm" in backup
    assert "metadata-postgres/daily/" not in backup + restore
    assert "sha256sum" in backup
    assert "backup/metadata_postgres_uri" in restore
    assert "--set ON_ERROR_STOP=1" in restore
    assert "pg_restore --no-owner --single-transaction" in restore
    assert "DROP DATABASE IF EXISTS $database" in restore
    assert "sha256sum -c" in restore
    assert "am | pm" in restore
    for source in (backup, restore):
        assert "git " not in source


def test_each_custom_component_has_a_thin_release_caller() -> None:
    expected = {
        "release-airflow.yml": (
            "component: airflow",
            "dockerfile: automation/airflow/runtime/Dockerfile",
            "repository: tgbao-dev-automation-airflow",
        ),
        "release-arxiv-lens.yml": (
            "component: arxiv-lens",
            "dockerfile: arxiv-lens/Dockerfile",
            "repository: tgbao-dev-arxiv-lens",
        ),
        "release-dbt.yml": (
            "component: dbt",
            "dockerfile: analytics/dbt-project/Dockerfile",
            "repository: tgbao-dev-analytics-dbt",
        ),
        "release-lightdash.yml": (
            "component: lightdash",
            "dockerfile: dockerfile",
            "external_source_revision: f57276359a0ffcf38c201f95503e671bf80910cd",
            "external_source_url: https://github.com/lightdash/lightdash",
            "platforms: linux/arm64",
            "repository: tgbao-dev-analytics-lightdash",
            "runner: ubuntu-24.04-arm",
        ),
        "release-lakehouse-ingest.yml": (
            "component: lakehouse-ingest",
            "dockerfile: lakehouse/ingest/Dockerfile",
            "repository: tgbao-dev-lakehouse-ingest",
            "- pyproject.toml",
            "- uv.lock",
        ),
        "release-t0-trading.yml": (
            "component: t0-trading",
            "dockerfile: t0-trading/Dockerfile",
            "repository: tgbao-dev-t0-trading",
            "- pyproject.toml",
            "- uv.lock",
        ),
    }

    for workflow, assertions in expected.items():
        source = _workflow(workflow)
        assert "uses: ./.github/workflows/_release-image.yml" in source
        assert "branches: [main]" in source
        assert ".github/actions/deploy-component/**" not in source
        assert source.count(".github/workflows/_release-image.yml") == 1
        assert "infra/runtime/delivery/**" not in source
        for assertion in assertions:
            assert assertion in source


def test_arxiv_lens_release_tracks_only_its_owned_contracts() -> None:
    caller = _workflow("release-arxiv-lens.yml")
    dockerfile = Path("arxiv-lens/Dockerfile").read_text(encoding="utf-8")

    for contract in (
        "lakehouse/contracts/curated/arxiv.yaml",
        "lakehouse/contracts/sources/arxiv.yaml",
    ):
        assert f"- {contract}" in caller
        assert f"COPY {contract} ./{contract}" in dockerfile
    assert "lakehouse/contracts/**" not in caller
    assert "COPY lakehouse/contracts ./lakehouse/contracts" not in dockerfile


def test_airflow_deploy_only_changes_reuse_the_runtime_image() -> None:
    caller = _workflow("release-airflow.yml")
    reusable = _workflow("_release-image.yml")

    assert "automation/airflow/pyproject.toml" in caller
    assert "automation/airflow/uv.lock" in caller
    assert "automation/airflow/runtime/**" in caller
    assert "automation/airflow/deploy/**" in caller
    assert "infra/runtime/postgres/**" in caller
    assert '[[ -z "$revision" && "$COMPONENT" == "airflow" ]]' in reusable
    assert "steps.image_source.outputs.tag" in reusable
    assert "aws ecr put-image" in reusable


def test_lightdash_release_builds_the_pinned_upstream_source_natively() -> None:
    reusable = _workflow("_release-image.yml")
    lightdash = _workflow("release-lightdash.yml")
    images_makefile = Path("make/images.mk").read_text(encoding="utf-8")

    upstream = "https://github.com/lightdash/lightdash.git#f57276359a0ffcf38c201f95503e671bf80910cd"
    assert (
        '[[ "$BUILD_CONTEXT" == "$EXTERNAL_SOURCE_URL.git#$EXTERNAL_SOURCE_REVISION" ]]' in reusable
    )
    assert f"build_context: {upstream}" in lightdash
    assert "external_source_revision: f57276359a0ffcf38c201f95503e671bf80910cd" in lightdash
    assert "external_source_url: https://github.com/lightdash/lightdash" in lightdash
    assert "SOURCE_TAG:" in reusable
    assert "--image-manifest-media-type" in reusable
    assert f"LIGHTDASH_BUILD_CONTEXT := {upstream}" in images_makefile
    assert "--tag lightdash:local" in images_makefile
    assert "lightdash/lightdash:latest" not in lightdash
    assert "build_timeout_minutes: 120" in lightdash


def test_lightdash_skills_match_the_pinned_runtime_cli() -> None:
    for skill in ("developing-in-lightdash", "effective-dbt-sql", "upgrade-preflight"):
        manifest = json.loads(
            Path(f".codex/skills/{skill}/.lightdash-skill-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["version"] == "1.146.0"

    makefile = Path("Makefile").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "LIGHTDASH_CLI_VERSION := 1.146.0" in makefile
    assert "@lightdash/cli@1.146.0" in workflow


def test_lightdash_content_and_runtime_have_separate_owners() -> None:
    release = _workflow("release-lightdash.yml")
    project_delivery = _workflow("deploy-lightdash-projects.yml")

    root = Path("analytics/lightdash")
    assert (root / "projects/engineering/content").is_dir()
    assert (root / "projects/research/content").is_dir()
    assert not (root / "deploy/projects.py").exists()
    assert not (root / "deploy/environments/dev.yml").exists()
    assert "analytics/lightdash/projects/**" not in release
    assert "analytics/lightdash/deploy/**" not in project_delivery
    assert "analytics/lightdash/projects/**" in project_delivery


def test_lightdash_projects_use_protected_stateless_delivery() -> None:
    workflow = _workflow("deploy-lightdash-projects.yml")
    workflow_config = yaml.safe_load(workflow)
    github_environment = Path("infra/terraform/github/environments/dev/main.tf").read_text(
        encoding="utf-8"
    )

    assert "environment: dev" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "id-token: write" in workflow
    assert "AWS_LIGHTDASH_DEPLOYER_ROLE_ARN" in workflow
    assert (
        "aws-actions/aws-secretsmanager-get-secrets@"
        "2cb1a461cbd4865ac4299648312e4704c646cd53" in workflow
    )
    assert "LIGHTDASH_CI_SECRET_ID" in workflow
    assert "tailscale/github-action@780049a30b6ff5c378a9e7b389d15ece7a204888" in workflow
    assert "ping: tgbao-dev-services" in workflow
    assert "npm install --global @lightdash/cli@1.146.0" in workflow
    assert (
        'echo "$GITHUB_WORKSPACE/analytics/dbt-project/runtime/.venv/bin" >> "$GITHUB_PATH"'
        in workflow
    )
    assert "cancel-in-progress: false" in workflow
    assert "strategy:" in workflow
    assert "fail-fast: false" in workflow
    assert "max-parallel: 2" in workflow
    assert "LIGHTDASH_PROJECT: ${{ matrix.project_uuid }}" in workflow
    assert "lightdash config get-project" in workflow
    assert workflow.count("lightdash deploy") == 1
    assert workflow.count("lightdash upload") == 1
    assert workflow.count("lightdash validate") == 1
    assert workflow.count("--force") == 1
    assert workflow.count("--skip-dbt-compile") == 2
    assert workflow.count("--skip-warehouse-catalog") == 2
    assert workflow.count("--no-partial-compilation") == 2
    assert workflow.count("--show-chart-configuration-warnings") == 1
    projects = workflow_config["jobs"]["deploy"]["strategy"]["matrix"]["include"]
    projects_root = Path("analytics/lightdash/projects")
    managed_domains = {path.name for path in projects_root.iterdir() if path.is_dir()}
    assert {project["domain"] for project in projects} == managed_domains
    assert len({project["project_uuid"] for project in projects}) == len(projects)
    for project in projects:
        domain = project["domain"]
        assert project["schema"] == f"analytics_{domain}"
        assert project["content_dir"] == f"analytics/lightdash/projects/{domain}/content"
        assert re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", project["project_uuid"])
    assert "lightdash login" not in workflow
    for variable in ("LIGHTDASH_CI_SECRET_ID", "LIGHTDASH_URL"):
        assert variable in github_environment
    assert "LIGHTDASH_ENGINEERING_PROJECT" not in github_environment
    assert "LIGHTDASH_RESEARCH_PROJECT" not in github_environment
    assert "http://tgbao-dev-services:8081" in github_environment


def test_lightdash_ci_token_has_an_owned_secret_sync_boundary() -> None:
    sync = Path("analytics/lightdash/deploy/sync-ci-secret").read_text(encoding="utf-8")
    services = Path("make/services.mk").read_text(encoding="utf-8")

    assert 'secret_id="lakehouse/$environment/lightdash/ci"' in sync
    assert 'keys | sort == ["api_key", "version"]' in sync
    assert 'startswith("ldpat_")' in sync
    assert '--secret-string "file://$payload_file"' in sync
    assert "lightdash-ci-secret-sync:" in services
    assert ".secrets/$(LAKEHOUSE_ENVIRONMENT)/lightdash/ci.json" in services


def test_t0_trading_has_an_owned_ssi_secret_sync_boundary() -> None:
    sync = Path("t0-trading/deploy/sync-ssi-secret").read_text(encoding="utf-8")
    services = Path("make/services.mk").read_text(encoding="utf-8")

    assert 'secret_id="lakehouse/$environment/t0-trading/ssi"' in sync
    assert 'has("private_key")' not in sync
    assert '"private_key"' not in sync
    assert 'keys | sort == ["api_key", "api_secret", "client_id", "version"]' in sync
    assert "{version, client_id, api_key, api_secret}" in sync
    assert "--secret-string file:///dev/stdin" in sync
    assert "t0-trading-ssi-secret-sync:" in services
    assert ".secrets/$(LAKEHOUSE_ENVIRONMENT)/t0-trading/ssi.json" in services


def test_docs_deployment_loads_cloudflare_token_through_aws_oidc() -> None:
    workflow = _workflow("deploy-docs.yml")
    sync = Path("docs/deploy/sync-ci-secret").read_text(encoding="utf-8")
    makefile = Path("make/docs.mk").read_text(encoding="utf-8")
    github_environment = Path("infra/terraform/github/environments/dev/main.tf").read_text(
        encoding="utf-8"
    )

    assert "id-token: write" in workflow
    assert "AWS_DOCS_DEPLOYER_ROLE_ARN" in workflow + github_environment
    assert "CLOUDFLARE_DOCS_CI_SECRET_ID" in workflow + github_environment
    assert "aws-actions/configure-aws-credentials@" in workflow
    assert "aws-actions/aws-secretsmanager-get-secrets@" in workflow
    assert "secrets.CLOUDFLARE_API_TOKEN" not in workflow
    assert 'secret_id="lakehouse/$environment/cloudflare/docs-ci"' in sync
    assert 'keys | sort == ["api_token", "version"]' in sync
    assert '--secret-string "file://$payload_file"' in sync
    assert "cloudflare-docs-ci-secret-sync:" in makefile
    assert ".secrets/$(LAKEHOUSE_ENVIRONMENT)/cloudflare/docs-ci.json" in makefile


def test_emr_has_an_independent_release_pointer() -> None:
    source = _workflow("release-emr-jobs.yml")
    makefile = Path("make/data.mk").read_text(encoding="utf-8")
    package = Path("lakehouse/emr/release/package").read_text(encoding="utf-8")
    publish = Path("lakehouse/emr/release/publish").read_text(encoding="utf-8")

    assert "AWS_EMR_PUBLISHER_ROLE_ARN" in source
    assert "environment: dev" not in source
    assert "dev-emr-jobs-publish" in source
    assert "queue: max" in source
    assert "EMR_ARTIFACTS_URI" in source
    assert "EMR_CODE_PARAMETER_NAME" in source
    assert "lakehouse/emr/release/package" in source
    assert "lakehouse/emr/release/publish" in source
    assert "--target artifacts" in package
    assert "--if-none-match '*'" in publish
    assert "SHA256SUMS" in publish
    assert "aws ssm put-parameter" in publish
    assert "emr-jobs-publish:" not in makefile
    assert "terraform output" not in makefile


def test_manual_rollback_requires_an_exact_image_and_revision() -> None:
    source = _workflow("rollback-component.yml")

    assert "workflow_dispatch:" in source
    assert "environment: dev" in source
    assert "dev-${{ inputs.component }}-deploy" in source
    assert "image:" in source
    assert "revision:" in source
    assert "ref: ${{ inputs.revision }}" in source
    assert "source_root: .release-source" in source
    assert "sparse-checkout:" in source
    assert "fetch-depth: 0" in source
    assert 'merge-base --is-ancestor "$REVISION" origin/main' in source
    assert "aws ecr batch-get-image" in source
    assert "aws sts get-caller-identity" in source
    assert '[[ "$IMAGE" == "$registry/$repository@$digest" ]]' in source
    assert "uses: ./.github/actions/deploy-component" in source


def test_manual_emr_rollback_restores_only_a_published_main_revision() -> None:
    source = _workflow("rollback-emr-jobs.yml")

    assert "workflow_dispatch:" in source
    assert "environment: dev" not in source
    assert "dev-emr-jobs-publish" in source
    assert "fetch-depth: 0" in source
    assert 'git merge-base --is-ancestor "$REVISION" origin/main' in source
    assert "aws s3api head-object" in source
    assert '"$prefix/SHA256SUMS"' in source
    assert "aws ssm put-parameter" in source
