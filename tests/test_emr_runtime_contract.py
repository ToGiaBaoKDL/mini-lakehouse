import ast
from pathlib import Path


def test_emr_artifacts_are_built_in_the_pinned_runtime() -> None:
    dockerfile = Path("jobs/emr/Dockerfile").read_text(encoding="utf-8")

    assert "amazonlinux:2023-minimal@sha256:" in dockerfile
    assert "dnf install -y --setopt=install_weak_deps=0 python3.11" in dockerfile
    assert ":latest" not in dockerfile
    assert "venv-pack" in dockerfile
    assert "python.tar.gz" in dockerfile

    makefile = Path("make/data.mk").read_text(encoding="utf-8")
    package = Path("jobs/emr/release/package").read_text(encoding="utf-8")
    assert "jobs/emr/.venv/lib/python" not in makefile
    assert "jobs/emr/release/package" in makefile
    assert 'jobs/emr/Dockerfile"' in package
    assert "emr_jobs.zip" not in makefile


def test_emr_artifact_sources_are_python_311_compatible() -> None:
    runtime_sources = (
        *Path("platform/src/lakehouse").rglob("*.py"),
        *Path("jobs/emr/src").rglob("*.py"),
        *Path("jobs/emr/entrypoints").glob("*.py"),
    )

    for path in runtime_sources:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 11),
        )


def test_emr_entrypoints_are_thin_python_adapters() -> None:
    entrypoints = sorted(Path("jobs/emr/entrypoints").glob("*.py"))
    assert {path.name for path in entrypoints} == {
        "arxiv_metadata.py",
        "github_archive.py",
        "iceberg_maintenance.py",
    }
    for path in entrypoints:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert any(
            isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("emr_jobs.")
            for node in tree.body
        )
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("CREATE TABLE" in node.value or "CREATE DATABASE" in node.value)
            for node in ast.walk(tree)
        )


def test_emr_uses_one_shared_iceberg_catalog_boundary() -> None:
    common = Path("jobs/emr/src/emr_jobs/common/iceberg.py").read_text(encoding="utf-8")
    entrypoints = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("jobs/emr/entrypoints").glob("*.py")
    )
    terraform = Path("infra/terraform/aws/modules/emr_serverless/variables.tf").read_text(
        encoding="utf-8"
    )

    assert "from lakehouse.catalog import CATALOG_NAME" in common
    assert "catalog_name" not in entrypoints
    assert 'default     = "glue"' in terraform
