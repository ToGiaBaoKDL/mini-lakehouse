import json
import re
from pathlib import Path

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


def test_component_release_publishes_before_protected_digest_deployment() -> None:
    release = _workflow("_release-image.yml")
    action = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
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
    assert "source-{0}" in release
    assert "cancel-in-progress: false" in release
    assert 'dbt-engineering) [[ "$BUILD_ARGS" == "DBT_PROJECT=engineering" ]]' in release
    assert 'dbt-research) [[ "$BUILD_ARGS" == "DBT_PROJECT=research" ]]' in release
    assert "^[0-9]{12}\\.dkr\\.ecr\\." in action
    assert "tag:tgbao-dev-ci" in action
    assert "tailscale ssh ubuntu@tgbao-dev-services" in action
    assert "deployment/release_manifest" not in release + action + script
    assert "latest" not in release + action + script
    assert 'tar -C "$SOURCE_ROOT" -cf -' in action
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
    assert "Image must be an immutable digest from the dev" in pull
    assert "services-deployer/host-config" in pull
    assert "AWS CLI v2 is missing from the services host." in pull


def test_host_workload_identities_ignore_operator_credentials() -> None:
    sources = [
        Path(path).read_text(encoding="utf-8")
        for path in (
            "infra/runtime/cloudflare/deploy",
            "infra/runtime/delivery/pull-image",
            "infra/runtime/postgres/deploy",
            "orchestration/deploy/reconcile",
            "apps/lightdash/deploy/reconcile",
        )
    ]

    assert all("AWS_SHARED_CREDENTIALS_FILE=/dev/null" in source for source in sources)


def test_modal_runner_keeps_models_cached_before_application_source() -> None:
    source = Path("ocr/runners/modal/glm_ocr/app.py").read_text(encoding="utf-8")

    assert ".run_function(" not in source
    assert source.index(".run_commands(") < source.index(".env(") < source.index(".add_local_dir(")
    assert 'MODEL_DOWNLOAD_COMMAND = shlex.join(("python", "-c", MODEL_DOWNLOAD_SCRIPT))' in source
    assert ".run_commands(MODEL_DOWNLOAD_COMMAND)" in source
    assert 'MODEL_DOWNLOAD_SCRIPT = ";".join(' in source
    assert '.env({"PYTHONPATH": str(RUNNER_ROOT)})' in source
    assert '.add_local_dir("ocr/config", "/root/ocr/config")' in source


def test_each_component_owns_its_deployment_operation() -> None:
    airflow = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("orchestration/deploy/deploy", "orchestration/deploy/reconcile")
    )
    inspector = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "apps/arxiv_inspector/deploy/deploy",
            "apps/arxiv_inspector/deploy/reconcile",
        )
    )
    dbt = Path("dbt/deploy/deploy").read_text(encoding="utf-8")
    ocr = Path("ocr/deploy/deploy").read_text(encoding="utf-8")
    postgres = Path("infra/runtime/postgres/deploy").read_text(encoding="utf-8")
    lightdash = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("apps/lightdash/deploy/deploy", "apps/lightdash/deploy/reconcile")
    )
    cloudflare = Path("infra/runtime/cloudflare/deploy").read_text(encoding="utf-8")

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
    assert "--force-recreate" not in lightdash
    assert "docker compose --project-name arxiv-inspector" in inspector
    assert '"$component:runtime"' in dbt
    assert "ocr-worker:runtime" in ocr
    assert "docker compose --project-name cloudflare" in cloudflare
    assert "--token-file" in Path("infra/runtime/cloudflare/compose.yaml").read_text(
        encoding="utf-8"
    )
    assert "git " not in airflow + inspector + lightdash + dbt + ocr + postgres + cloudflare


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
    assert "infra/runtime/cloudflare" in action
    assert "file:///dev/stdin" in sync
    assert "CLOUDFLARE_API_TOKEN" in sync
    assert 'secret-string "$token"' not in sync


def test_each_custom_component_has_a_thin_release_caller() -> None:
    expected = {
        "release-airflow.yml": (
            "component: airflow",
            "dockerfile: orchestration/runtime/Dockerfile",
        ),
        "release-arxiv-inspector.yml": (
            "component: arxiv-inspector",
            "dockerfile: apps/arxiv_inspector/Dockerfile",
        ),
        "release-dbt-engineering.yml": (
            "component: dbt-engineering",
            "dockerfile: dbt/Dockerfile",
            "build_args: DBT_PROJECT=engineering",
        ),
        "release-dbt-research.yml": (
            "component: dbt-research",
            "dockerfile: dbt/Dockerfile",
            "build_args: DBT_PROJECT=research",
        ),
        "release-ocr-worker.yml": (
            "component: ocr-worker",
            "dockerfile: ocr/Dockerfile",
        ),
        "release-lightdash.yml": (
            "component: lightdash",
            "dockerfile: dockerfile",
            "external_source_revision: f57276359a0ffcf38c201f95503e671bf80910cd",
            "external_source_url: https://github.com/lightdash/lightdash",
            "platforms: linux/arm64",
            "runner: ubuntu-24.04-arm",
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
    assert "apps/arxiv_inspector/pyproject.toml" in _workflow("release-ocr-worker.yml")


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

    assert "@lightdash/cli@1.146.0" in Path("infra/README.md").read_text(encoding="utf-8")


def test_emr_has_an_independent_release_pointer() -> None:
    source = _workflow("release-emr-jobs.yml")
    makefile = Path("make/data.mk").read_text(encoding="utf-8")
    package = Path("jobs/emr/release/package").read_text(encoding="utf-8")
    publish = Path("jobs/emr/release/publish").read_text(encoding="utf-8")

    assert "AWS_EMR_PUBLISHER_ROLE_ARN" in source
    assert "environment: dev" not in source
    assert "dev-emr-jobs-publish" in source
    assert "queue: max" in source
    assert "EMR_ARTIFACTS_URI" in source
    assert "EMR_CODE_PARAMETER_NAME" in source
    assert "jobs/emr/release/package" in source
    assert "jobs/emr/release/publish" in source
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
