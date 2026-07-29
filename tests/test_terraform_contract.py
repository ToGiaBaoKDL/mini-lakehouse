from pathlib import Path


def _terraform_sources(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.tf")))


def test_terraform_manages_infrastructure_but_not_glue_objects() -> None:
    source = _terraform_sources(Path("infra/terraform"))

    assert 'resource "aws_glue_catalog_database"' not in source
    assert 'resource "aws_glue_catalog_table"' not in source
    assert "cloudwatch" not in source.lower()
    assert 'resource "aws_emrserverless_application"' in source
    assert 'resource "aws_athena_workgroup"' not in source
    assert "enforce_workgroup_configuration" not in source
    assert 'resource "aws_s3_bucket"' in source


def test_reusable_modules_contain_no_source_specific_identifiers() -> None:
    modules = _terraform_sources(Path("infra/terraform/modules")).lower()

    assert "github" not in modules
    assert "arxiv" not in modules
    assert "curated_arxiv" not in modules
    assert "landing_github_archive" not in modules


def test_remote_state_is_versioned_locked_and_bootstrapped_separately() -> None:
    backend = Path("infra/terraform/environments/dev/backend.tf").read_text(encoding="utf-8")
    bootstrap = _terraform_sources(Path("infra/terraform/bootstrap/state"))

    assert 'backend "s3"' in backend
    assert "use_lockfile = true" in backend
    assert 'status = "Enabled"' in bootstrap
    assert "prevent_destroy = true" in bootstrap
    assert "BucketOwnerEnforced" in bootstrap


def test_ci_validation_does_not_require_remote_state() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "terraform-validate: ##" in makefile
    assert makefile.count("init -backend=false -lockfile=readonly") == 2
    assert "$(MAKE) terraform-validate" in makefile


def test_secret_containers_never_manage_secret_values() -> None:
    module = _terraform_sources(Path("infra/terraform/modules/secrets"))

    assert 'resource "aws_secretsmanager_secret"' in module
    assert "aws_secretsmanager_secret_version" not in module


def test_dev_resources_and_workload_roles_have_explicit_boundaries() -> None:
    environment = Path("infra/terraform/environments/dev/main.tf").read_text(encoding="utf-8")
    identity = Path("infra/terraform/modules/identity/main.tf").read_text(encoding="utf-8")
    normalized_environment = " ".join(environment.split())
    normalized_identity = " ".join(identity.split())

    assert 'name_prefix = "${local.project}-${local.environment}"' in normalized_environment
    assert (
        'bucket_tiers = ["landing", "curated", "analytics", "artifacts", "query-results"]'
        in normalized_environment
    )
    assert 'parameter_prefix = "/lakehouse/${local.environment}"' in normalized_environment
    assert 'athena_workgroup = "primary"' in normalized_environment
    assert 'athena_query_results_prefix = "athena/primary"' in normalized_environment
    assert '"storage/query_results_uri"' in environment
    assert "athena/workgroups/" not in environment
    for role in (
        "emr-runtime",
        "emr-deployer",
        "airflow",
        "catalog-admin",
        "dbt-transformer",
        "document-inspector",
        "lightdash-reader",
    ):
        assert f'name = "${{var.name_prefix}}-{role}"' in normalized_identity

    assert identity.count("resources = [var.athena_workgroup_arn]") == 3
    assert '${var.athena_query_results_prefix}/*"' in identity
