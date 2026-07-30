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


def test_each_child_module_declares_its_provider_contract() -> None:
    expected_providers = {
        "storage": ('source  = "hashicorp/aws"', 'source  = "hashicorp/random"'),
        "emr_serverless": ('source  = "hashicorp/aws"',),
        "identity": ('source  = "hashicorp/aws"',),
    }

    for module, providers in expected_providers.items():
        versions = Path(f"infra/terraform/modules/{module}/versions.tf").read_text(encoding="utf-8")
        assert 'required_version = ">= 1.10"' in versions
        for provider in providers:
            assert provider in versions


def test_all_terraform_inputs_and_outputs_are_documented() -> None:
    for path in Path("infra/terraform").rglob("variables.tf"):
        source = path.read_text(encoding="utf-8")
        assert source.count('variable "') == source.count("description ="), path

    for path in Path("infra/terraform").rglob("outputs.tf"):
        source = path.read_text(encoding="utf-8")
        assert source.count('output "') == source.count("description ="), path


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
    environment = _terraform_sources(Path("infra/terraform/environments/dev"))

    assert 'resource "aws_secretsmanager_secret" "airflow_connection"' in environment
    assert "aws_secretsmanager_secret_version" not in environment
    assert "ocr/providers" not in environment


def test_dev_resources_and_workload_roles_have_explicit_boundaries() -> None:
    environment = _terraform_sources(Path("infra/terraform/environments/dev"))
    runtime = Path("infra/terraform/environments/dev/runtime.tf").read_text(encoding="utf-8")
    identity = _terraform_sources(Path("infra/terraform/modules/identity"))
    normalized_environment = " ".join(environment.split())
    normalized_identity = " ".join(identity.split())

    assert 'name_prefix = "${local.project}-${local.environment}"' in normalized_environment
    assert (
        'bucket_tiers = ["landing", "curated", "analytics", "artifacts", "query-results"]'
        in normalized_environment
    )
    assert 'parameter_prefix = "/lakehouse/${local.environment}"' in normalized_environment
    assert 'athena_workgroup = "primary"' in normalized_environment
    assert 'dbt_transformer = "dbt"' in normalized_environment
    assert 'document_inspector = "document-inspector"' in normalized_environment
    assert '"athena/dbt_output_uri"' in environment
    assert '"athena/document_inspector_output_uri"' in environment
    assert "athena/primary" not in environment
    assert "storage/query_results_uri" not in runtime
    assert "athena/workgroups/" not in environment
    for role in (
        "emr-runtime",
        "emr-deployer",
        "airflow",
        "catalog-admin",
        "dbt-transformer",
        "document-inspector",
    ):
        assert f'name = "${{var.name_prefix}}-{role}"' in normalized_identity

    assert identity.count("resources = [var.athena_workgroup_arn]") == 2
    assert "lightdash" not in identity.lower()
    assert "document_inspector_curated_object_arns" in identity
    assert "athena_result_prefixes.dbt_transformer" in identity
    assert "athena_result_prefixes.document_inspector" in identity


def test_environment_uses_only_domain_modules() -> None:
    modules = {path.name for path in Path("infra/terraform/modules").iterdir() if path.is_dir()}

    assert modules == {"emr_serverless", "identity", "storage"}


def test_runtime_parameters_and_trust_are_bounded_by_workload() -> None:
    environment = _terraform_sources(Path("infra/terraform/environments/dev"))
    identity = _terraform_sources(Path("infra/terraform/modules/identity"))

    assert 'check "runtime_parameter_grants"' in environment
    assert '"emr/code_uri"' in environment
    assert "managed_parameter_names" in environment
    assert "granted_parameter_names" in environment

    assert "for_each = var.trusted_principals" in identity
    for workload in (
        "airflow",
        "catalog_admin",
        "dbt_transformer",
        "document_inspector",
        "emr_deployer",
    ):
        assert f'operator_trust["{workload}"]' in identity
    assert "trusted_principal_arns" not in identity
