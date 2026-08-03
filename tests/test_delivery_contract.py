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


def test_component_release_is_environment_protected_and_digest_driven() -> None:
    release = _workflow("_release-image.yml")
    action = Path(".github/actions/deploy-component/action.yml").read_text(encoding="utf-8")
    script = Path("infra/runtime/delivery/deploy-component").read_text(encoding="utf-8")

    assert "environment: dev" in release
    assert "id-token: write" in release
    assert "github.ref == 'refs/heads/main'" in release
    assert "platforms: linux/amd64,linux/arm64" in release
    assert "provenance: mode=max" in release
    assert "sbom: true" in release
    assert "imageTag=$GITHUB_SHA" in release
    assert "cancel-in-progress: false" in release
    assert "^[0-9]{12}\\.dkr\\.ecr\\." in action
    assert "tag:tgbao-dev-ci" in action
    assert "tailscale ssh ubuntu@tgbao-dev-services" in action
    assert "deployment/release_manifest" not in release + action + script
    assert "latest" not in release + action + script


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
        "release-dbt-task.yml": (
            "component: dbt-task",
            "dockerfile: dbt/analytics/Dockerfile",
        ),
        "release-ocr-worker.yml": (
            "component: ocr-worker",
            "dockerfile: ocr/Dockerfile",
        ),
    }

    for workflow, assertions in expected.items():
        source = _workflow(workflow)
        assert "uses: ./.github/workflows/_release-image.yml" in source
        assert "branches: [main]" in source
        for assertion in assertions:
            assert assertion in source


def test_emr_has_an_independent_release_pointer() -> None:
    source = _workflow("release-emr-jobs.yml")
    makefile = Path("make/data.mk").read_text(encoding="utf-8")

    assert "AWS_EMR_PUBLISHER_ROLE_ARN" in source
    assert "EMR_ARTIFACTS_URI" in source
    assert "EMR_CODE_PARAMETER_NAME" in source
    assert "make emr-jobs-publish" in source
    assert "EMR_ARTIFACTS_URI and EMR_CODE_PARAMETER_NAME must be set together" in makefile


def test_manual_rollback_requires_an_exact_image_and_revision() -> None:
    source = _workflow("rollback-component.yml")

    assert "workflow_dispatch:" in source
    assert "environment: dev" in source
    assert "image:" in source
    assert "revision:" in source
    assert "ref: ${{ inputs.revision }}" not in source
    assert "uses: ./.github/actions/deploy-component" in source
