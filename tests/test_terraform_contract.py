from pathlib import Path


def _terraform_sources(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.tf")))


def test_terraform_manages_infrastructure_but_not_glue_objects() -> None:
    source = _terraform_sources(Path("infra/terraform"))

    assert 'resource "aws_glue_catalog_database"' not in source
    assert 'resource "aws_glue_catalog_table"' not in source
    assert "cloudwatch" not in source.lower()
    assert 'resource "aws_emrserverless_application"' in source
    assert 'resource "aws_ecr_repository"' in source
    assert 'resource "aws_athena_workgroup"' not in source
    assert "enforce_workgroup_configuration" not in source
    assert 'resource "aws_s3_bucket"' in source


def test_reusable_modules_contain_no_ingestion_table_identifiers() -> None:
    modules = _terraform_sources(Path("infra/terraform/modules")).lower()

    assert "github" not in modules
    assert "curated_arxiv" not in modules
    assert "landing_github_archive" not in modules


def test_each_child_module_declares_its_provider_contract() -> None:
    expected_providers = {
        "container_registry": ('source  = "hashicorp/aws"',),
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

    assert 'resource "aws_secretsmanager_secret" "airflow"' in environment
    assert 'resource "aws_secretsmanager_secret" "ocr"' in environment
    assert '"lakehouse/${local.environment}/airflow/bootstrap"' in environment
    assert '"lakehouse/${local.environment}/airflow/connections/${connection}"' in environment
    assert '"lakehouse/${local.environment}/ocr/providers/${each.key}"' in environment
    assert "aws_secretsmanager_secret_version" not in environment


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
    assert 'arxiv_inspector = "arxiv-inspector"' in normalized_environment
    assert '"athena/dbt_output_uri"' in environment
    assert '"athena/arxiv_inspector_output_uri"' in environment
    assert "athena/primary" not in environment
    assert "storage/query_results_uri" not in runtime
    assert "athena/workgroups/" not in environment
    for role in (
        "emr-runtime",
        "emr-deployer",
        "airflow",
        "catalog-admin",
        "dbt-transformer",
        "arxiv-inspector",
        "image-publisher",
        "ocr-worker",
    ):
        assert f'name = "${{var.name_prefix}}-{role}"' in normalized_identity

    assert identity.count("resources = [var.athena_workgroup_arn]") == 2
    assert "lightdash" not in identity.lower()
    assert "arxiv_inspector_curated_object_arns" in identity
    assert "athena_result_prefixes.dbt_transformer" in identity
    assert "athena_result_prefixes.arxiv_inspector" in identity


def test_environment_uses_only_domain_modules() -> None:
    modules = {path.name for path in Path("infra/terraform/modules").iterdir() if path.is_dir()}

    assert modules == {"container_registry", "emr_serverless", "identity", "storage"}


def test_runtime_parameters_and_trust_are_bounded_by_workload() -> None:
    environment = _terraform_sources(Path("infra/terraform/environments/dev"))
    identity = _terraform_sources(Path("infra/terraform/modules/identity"))

    assert 'check "runtime_parameter_grants"' in environment
    assert '"emr/code_uri"' in environment
    assert '"secrets/airflow_bootstrap_id"' in environment
    assert "managed_parameter_names" in environment
    assert "granted_parameter_names" in environment

    assert "for_each = var.role_trust" in identity
    for workload in (
        "airflow",
        "catalog_admin",
        "dbt_transformer",
        "arxiv_inspector",
        "emr_deployer",
        "image_publisher",
        "ocr_worker",
    ):
        assert f'operator_trust["{workload}"]' in identity
    assert "trusted_principals" not in identity


def test_service_images_are_immutable_bounded_and_published_by_one_role() -> None:
    environment = _terraform_sources(Path("infra/terraform/environments/dev"))
    registry = _terraform_sources(Path("infra/terraform/modules/container_registry"))
    identity = _terraform_sources(Path("infra/terraform/modules/identity"))

    assert 'repositories         = ["airflow", "arxiv-inspector", "ocr-worker"]' in environment
    assert 'image_tag_mutability = "IMMUTABLE"' in registry
    assert "scan_on_push = true" in registry
    assert 'encryption_type = "AES256"' in registry
    assert 'countType   = "imageCountMoreThan"' in registry
    assert "retained_image_count = 20" in environment
    assert '"${var.name_prefix}-image-publisher"' in identity
    assert '"ecr:GetAuthorizationToken"' in identity
    assert '"ecr:PutImage"' in identity
    assert "container_repository_arns" in identity


def test_airflow_and_ocr_worker_have_separate_data_permissions() -> None:
    airflow = Path("infra/terraform/modules/identity/airflow.tf").read_text(encoding="utf-8")
    ocr = Path("infra/terraform/modules/identity/ocr.tf").read_text(encoding="utf-8")

    assert "UpdateCuratedIceberg" not in airflow
    assert "UpdateCuratedStorage" not in airflow
    assert "ReadProviderCredentials" not in airflow
    assert "UpdateCuratedIceberg" in ocr
    assert "UpdateCuratedStorage" in ocr
    assert "ReadProviderCredentials" in ocr
