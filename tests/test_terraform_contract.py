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
    assert "hashicorp/random" not in source
    assert 'resource "random_string"' not in source


def test_reusable_modules_contain_no_ingestion_table_identifiers() -> None:
    modules = _terraform_sources(Path("infra/terraform/aws/modules")).lower()

    assert "github_archive" not in modules
    assert "curated_arxiv" not in modules
    assert "landing_github_archive" not in modules


def test_each_child_module_declares_its_provider_contract() -> None:
    expected_providers = {
        "container_registry": ('source  = "hashicorp/aws"',),
        "storage": ('source  = "hashicorp/aws"',),
        "emr_serverless": ('source  = "hashicorp/aws"',),
        "identity": ('source  = "hashicorp/aws"',),
    }

    for module, providers in expected_providers.items():
        versions = Path(f"infra/terraform/aws/modules/{module}/versions.tf").read_text(
            encoding="utf-8"
        )
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
    backend = Path("infra/terraform/aws/environments/dev/backend.tf").read_text(encoding="utf-8")
    bootstrap = _terraform_sources(Path("infra/terraform/aws/bootstrap/state"))
    makefile = Path("make/infra.mk").read_text(encoding="utf-8")

    assert 'backend "s3"' in backend
    assert 'key          = "lakehouse/aws/dev/terraform.tfstate"' in backend
    assert "use_lockfile = true" in backend
    assert 'backend "local"' in bootstrap
    assert 'status = "Enabled"' in bootstrap
    assert "prevent_destroy = true" in bootstrap
    assert "BucketOwnerEnforced" in bootstrap
    assert "aws-bootstrap.tfstate" in makefile
    assert 'init -backend-config="path=$(AWS_STATE_FILE)"' in makefile


def test_ci_validation_does_not_require_remote_state() -> None:
    makefile = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("Makefile"), *sorted(Path("make").glob("*.mk")))
    )

    assert "terraform-validate: terraform-cache ##" in makefile
    assert makefile.count("init -backend=false -lockfile=readonly") == 1
    assert makefile.count('validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/') == 6
    assert "$(MAKE) terraform-validate" in makefile


def test_secret_containers_never_manage_secret_values() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))

    assert 'resource "aws_secretsmanager_secret" "airflow"' in environment
    assert 'resource "aws_secretsmanager_secret" "metadata_postgres"' in environment
    assert 'resource "aws_secretsmanager_secret" "ocr"' in environment
    assert 'resource "aws_secretsmanager_secret" "cloudflare_tunnel"' in environment
    assert '"lakehouse/${local.environment}/airflow/runtime"' in environment
    assert '"lakehouse/${local.environment}/airflow/connections/${connection}"' in environment
    assert '"lakehouse/${local.environment}/metadata-postgres/${each.key}"' in environment
    assert '"lakehouse/${local.environment}/ocr/providers/${each.key}"' in environment
    assert '"lakehouse/${local.environment}/cloudflare/tunnel-token"' in environment
    assert "aws_secretsmanager_secret_version" not in environment


def test_dev_resources_and_workload_roles_have_explicit_boundaries() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    runtime = Path("infra/terraform/aws/environments/dev/runtime.tf").read_text(encoding="utf-8")
    identity = _terraform_sources(Path("infra/terraform/aws/modules/identity"))
    normalized_environment = " ".join(environment.split())
    normalized_identity = " ".join(identity.split())

    assert 'name_prefix = "${local.project}-${local.environment}"' in normalized_environment
    for tier in ("landing", "curated", "analytics", "artifacts", "logs", "query-results"):
        assert f"${{local.name_prefix}}-{tier}-" in normalized_environment
    assert "hashicorp/random" not in environment
    assert 'parameter_prefix = "/lakehouse/${local.environment}"' in normalized_environment
    assert 'athena_workgroup = "primary"' in normalized_environment
    assert 'dbt_transformer = "dbt"' in normalized_environment
    assert 'arxiv_inspector = "arxiv-inspector"' in normalized_environment
    assert '"athena/dbt_output_uri"' in environment
    assert '"athena/arxiv_inspector_output_uri"' in environment
    assert '"airflow/remote_log_uri"' in environment
    assert '"deployment/release_manifest"' not in environment
    assert "workload_data_access = local.workload_data_access" in normalized_environment
    assert "notifications/alert_email" not in environment
    assert "notifications/slack_channel" not in environment
    assert '"catalog/name"' not in environment
    assert "athena/primary" not in environment
    assert "storage/query_results_uri" not in runtime
    assert "athena/workgroups/" not in environment
    for role in (
        "emr-runtime",
        "airflow",
        "catalog-admin",
        "services-deployer",
        "dbt-transformer",
        "arxiv-inspector",
        "metadata-postgres",
        "ocr-worker",
        "github-image-publisher",
        "github-emr-publisher",
    ):
        assert f'name = "${{var.name_prefix}}-{role}"' in normalized_identity

    assert identity.count("resources = [var.athena_workgroup_arn]") == 2
    assert "lightdash" not in identity.lower()
    assert "curated_object_arns_by_workload" in identity
    assert "analytics_object_arns_by_workload" in identity
    assert "athena_result_prefixes.dbt_transformer" in identity
    assert "athena_result_prefixes.arxiv_inspector" in identity


def test_environment_uses_only_domain_modules() -> None:
    modules = {path.name for path in Path("infra/terraform/aws/modules").iterdir() if path.is_dir()}

    assert modules == {"container_registry", "emr_serverless", "identity", "storage"}


def test_runtime_parameters_and_trust_are_bounded_by_workload() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    identity = _terraform_sources(Path("infra/terraform/aws/modules/identity"))

    assert 'check "runtime_parameter_grants"' in environment
    assert '"emr/code_uri"' in environment
    assert '"secrets/airflow_bootstrap_id"' not in environment
    assert '"catalog/name"' not in environment
    assert "managed_parameter_names" in environment
    assert "granted_parameter_names" in environment

    assert "identifiers = var.catalog_admin_principal_arns" in identity
    assert "role_trust" not in identity
    assert "source_policy_documents" not in identity
    assert "external_runtime_trust" in identity
    assert "rolesanywhere.amazonaws.com" in identity
    assert "aws:PrincipalTag/x509Subject/CN" in identity
    assert "trusted_principals" not in identity
    assert 'actions = ["sts:AssumeRoleWithWebIdentity"]' in identity
    assert 'variable = "token.actions.githubusercontent.com:aud"' in identity
    assert 'variable = "token.actions.githubusercontent.com:sub"' in identity
    assert "github_environment_subject" in environment
    assert "github_environment_subject" in identity


def test_service_images_are_immutable_bounded_and_published_by_one_role() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    registry = _terraform_sources(Path("infra/terraform/aws/modules/container_registry"))
    identity = _terraform_sources(Path("infra/terraform/aws/modules/identity"))

    assert (
        'repositories         = ["airflow", "arxiv-inspector", "dbt-task", "ocr-worker"]'
        in environment
    )
    assert 'image_tag_mutability = "IMMUTABLE"' in registry
    assert "scan_on_push = true" in registry
    assert 'encryption_type = "AES256"' in registry
    assert 'countType      = "imageCountMoreThan"' in registry
    assert 'tagStatus      = "tagged"' in registry
    assert 'tagPatternList = ["*"]' in registry
    assert 'tagStatus   = "untagged"' in registry
    assert 'countType   = "sinceImagePushed"' in registry
    assert "retained_image_count = 20" in environment
    assert '"${var.name_prefix}-github-image-publisher"' in identity
    assert '"${var.name_prefix}-image-publisher"' not in identity
    assert '"${var.name_prefix}-emr-deployer"' not in identity
    assert '"ecr:GetAuthorizationToken"' in identity
    assert '"ecr:PutImage"' in identity
    assert '"ecr:DescribeImages"' not in identity
    assert "container_repository_arns" in identity
    assert "PublishReleaseManifest" not in identity
    assert "ssm:PutParameter" not in Path(
        "infra/terraform/aws/modules/identity/images.tf"
    ).read_text(encoding="utf-8")


def test_services_deployer_reads_only_the_connector_secret() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    services = Path("infra/terraform/aws/modules/identity/services.tf").read_text(encoding="utf-8")

    assert "services_deployer_secret_arns" in environment + services
    assert "aws_secretsmanager_secret.cloudflare_tunnel.arn" in environment
    assert 'sid       = "ReadInfrastructureConnectorSecrets"' in services
    assert 'actions   = ["secretsmanager:GetSecretValue"]' in services
    assert "airflow_secret_arns" not in services
    assert "ocr_secret_arns" not in services


def test_airflow_and_ocr_worker_have_separate_data_permissions() -> None:
    airflow = Path("infra/terraform/aws/modules/identity/airflow.tf").read_text(encoding="utf-8")
    ocr = Path("infra/terraform/aws/modules/identity/ocr.tf").read_text(encoding="utf-8")
    postgres = Path("infra/terraform/aws/modules/identity/postgres.tf").read_text(encoding="utf-8")

    assert "UpdateCuratedIceberg" not in airflow
    assert "UpdateCuratedStorage" not in airflow
    assert "ReadProviderCredentials" not in airflow
    assert "UpdateCuratedIceberg" in ocr
    assert "UpdateCuratedStorage" in ocr
    assert "ReadProviderCredentials" in ocr
    assert "local.curated_database_arns_by_workload.ocr_worker" in ocr
    assert "local.curated_object_arns_by_workload.ocr_worker" in ocr
    assert '"${var.bucket_arns.curated}/*"' not in ocr
    assert "secretsmanager:DescribeSecret" not in airflow + ocr + postgres


def test_data_consumers_use_reviewed_database_and_prefix_entitlements() -> None:
    dbt = Path("infra/terraform/aws/modules/identity/dbt.tf").read_text(encoding="utf-8")
    inspector = Path("infra/terraform/aws/modules/identity/arxiv_inspector.tf").read_text(
        encoding="utf-8"
    )

    assert "local.curated_database_arns_by_workload.dbt_transformer" in dbt
    assert "local.analytics_database_arns_by_workload.dbt_transformer" in dbt
    assert "local.curated_prefixes_by_workload.dbt_transformer" in dbt
    assert "local.analytics_prefixes_by_workload.dbt_transformer" in dbt
    assert '"${var.bucket_arns.curated}/*"' not in dbt
    assert '"${var.bucket_arns.analytics}/*"' not in dbt
    assert "local.curated_database_arns_by_workload.arxiv_inspector" in inspector
    assert "local.curated_prefixes_by_workload.arxiv_inspector" in inspector


def test_cloud_roots_have_isolated_state_and_private_access_host() -> None:
    oci = _terraform_sources(Path("infra/terraform/oci/environments/dev"))
    cloud_init = Path("infra/terraform/oci/environments/dev/cloud-init.yaml.tftpl").read_text(
        encoding="utf-8"
    )
    aws_cli_installer = Path("infra/runtime/host/install-aws-cli").read_text(encoding="utf-8")
    signing_helper_installer = Path("infra/runtime/identity/install-aws-signing-helper").read_text(
        encoding="utf-8"
    )
    tailscale_installer = Path("infra/runtime/host/install-tailscale").read_text(encoding="utf-8")
    tailscale = _terraform_sources(Path("infra/terraform/tailscale/environments/dev"))
    github = _terraform_sources(Path("infra/terraform/github/environments/dev"))
    cloudflare = _terraform_sources(Path("infra/terraform/cloudflare/environments/dev"))
    normalized_oci = " ".join(oci.split())

    assert 'key          = "lakehouse/oci/dev/terraform.tfstate"' in oci
    assert 'key          = "lakehouse/tailscale/dev/terraform.tfstate"' in tailscale
    assert 'key          = "lakehouse/github/dev/terraform.tfstate"' in github
    assert 'key          = "lakehouse/cloudflare/dev/terraform.tfstate"' in cloudflare
    assert 'key     = "lakehouse/tailscale/dev/terraform.tfstate"' in oci
    assert 'variable "tailscale_auth_key"' not in oci
    assert 'shape = "VM.Standard.A1.Flex"' in normalized_oci
    assert "ocpus = 4" in normalized_oci
    assert "memory_in_gbs = 24" in normalized_oci
    assert "source_id               = var.image_ocid" in oci
    assert 'data "oci_core_images"' not in oci
    assert 'variable "ssh_authorized_key"' not in oci
    assert "ssh_authorized_keys" not in oci
    assert "assign_public_ip = true" in oci
    assert 'resource "oci_core_internet_gateway" "services"' in oci
    assert "ingress_security_rules" not in oci
    assert 'version = "~> 8.23.0"' in oci
    assert 'version = "~> 0.29.2"' in tailscale
    assert '"tcp:22"' in tailscale
    assert '"tcp:8080"' in tailscale
    assert '"tcp:8501"' in tailscale
    assert "reusable            = false" in tailscale
    assert 'recreate_if_invalid = "always"' in tailscale
    assert 'services_tag = "tag:tgbao-dev-services"' in tailscale
    assert 'ci_tag       = "tag:tgbao-dev-ci"' in tailscale
    assert 'resource "tailscale_federated_identity" "github_deployer"' in tailscale
    assert 'ref           = "refs/heads/main"' in tailscale
    assert "repository_id = local.github_repository.id" in tailscale
    assert 'name = "tgbao-dev-services"' in normalized_oci
    assert 'dns_label = "services"' in normalized_oci
    assert "  - git" not in cloud_init
    assert "  - make" not in cloud_init
    assert "  - awscli" not in cloud_init
    assert "/usr/local/sbin/install-aws-cli" in cloud_init
    assert 'version="2.36.11"' in aws_cli_installer
    assert "awscli-exe-linux-$distribution_architecture-$version.zip" in aws_cli_installer
    assert "FB5DB77FD5C118B80511ADA8A6310ACC4672475C" in aws_cli_installer
    assert 'version="1.8.4"' in signing_helper_installer
    assert '"$target" version' in signing_helper_installer
    assert "python3 -" in signing_helper_installer
    assert "/usr/local/sbin/install-tailscale" in cloud_init
    assert 'version="1.102.1"' in tailscale_installer
    assert "/opt/lakehouse" not in cloud_init
    assert "--auth-key=file:/run/tailscale-auth-key" in cloud_init


def test_cloudflare_edge_uses_current_tunnel_and_access_resources() -> None:
    cloudflare = _terraform_sources(Path("infra/terraform/cloudflare/environments/dev"))

    assert 'version = "~> 5.22.0"' in cloudflare
    assert 'resource "cloudflare_zero_trust_tunnel_cloudflared" "services"' in cloudflare
    assert 'resource "cloudflare_zero_trust_tunnel_cloudflared_config" "services"' in cloudflare
    assert 'resource "cloudflare_dns_record" "application"' in cloudflare
    assert 'resource "cloudflare_zero_trust_access_application" "application"' in cloudflare
    assert 'config_src = "cloudflare"' in cloudflare
    assert 'source     = "cloudflare"' in cloudflare
    assert 'service = "http_status:404"' in cloudflare
    assert (
        'content = "${cloudflare_zero_trust_tunnel_cloudflared.services.id}.cfargotunnel.com"'
        in cloudflare
    )
    assert 'type = "public"' in cloudflare
    assert 'decision   = "allow"' in cloudflare
    assert "self_hosted_domains" not in cloudflare
    assert 'variable "cloudflare_api_token"' not in cloudflare
    assert "tunnel_secret" not in cloudflare
    assert 'output "account_id"' in cloudflare


def test_github_delivery_configuration_is_terraform_owned_and_state_derived() -> None:
    github = _terraform_sources(Path("infra/terraform/github/environments/dev"))

    assert 'version = "~> 6.13.0"' in github
    assert "hashicorp/aws" not in github
    assert 'data "aws_caller_identity"' not in github
    assert 'variable "state_bucket"' in github
    assert 'data "github_repository" "this"' in github
    assert 'resource "github_repository"' not in github
    assert 'resource "github_repository_environment" "dev"' in github
    assert 'resource "github_repository_environment_deployment_policy" "main"' in github
    assert 'branch_pattern = "main"' in github
    assert "can_admins_bypass   = false" in github
    assert "users = [data.github_user.owner.id]" in github
    assert 'data "terraform_remote_state" "aws"' in github
    assert 'data "terraform_remote_state" "tailscale"' in github
    assert 'resource "github_actions_environment_variable" "release"' in github
    for variable in (
        "AWS_EMR_PUBLISHER_ROLE_ARN",
        "AWS_IMAGE_PUBLISHER_ROLE_ARN",
        "EMR_ARTIFACTS_URI",
        "EMR_CODE_PARAMETER_NAME",
        "TAILSCALE_AUDIENCE",
        "TAILSCALE_CLIENT_ID",
    ):
        assert variable in github


def test_provider_authentication_is_not_a_terraform_input() -> None:
    aws_environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    aws_bootstrap = _terraform_sources(Path("infra/terraform/aws/bootstrap/state"))
    oci_environment = _terraform_sources(Path("infra/terraform/oci/environments/dev"))
    github_environment = _terraform_sources(Path("infra/terraform/github/environments/dev"))
    cloudflare_environment = _terraform_sources(Path("infra/terraform/cloudflare/environments/dev"))
    examples = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("infra/terraform").rglob("terraform.tfvars.example")
    )

    assert 'variable "aws_profile"' not in aws_environment + aws_bootstrap
    assert "profile = var.aws_profile" not in aws_environment + aws_bootstrap
    assert 'variable "oci_profile"' not in oci_environment
    assert "config_file_profile = var.oci_profile" not in oci_environment
    assert 'variable "github_token"' not in github_environment
    assert "token =" not in github_environment
    assert 'variable "cloudflare_api_token"' not in cloudflare_environment
    assert "api_token =" not in cloudflare_environment
    assert "aws_profile" not in examples
    assert "oci_profile" not in examples


def test_operator_commands_use_the_standard_aws_credential_chain() -> None:
    makefile = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("Makefile"), *sorted(Path("make").glob("*.mk")))
    )

    assert "CATALOG_ADMIN_AWS_PROFILE" not in makefile
    assert "EMR_DEPLOYER_AWS_PROFILE" not in makefile
    assert "IMAGE_PUBLISHER_AWS_PROFILE" not in makefile
    assert "aws --profile" not in makefile


def test_reviewable_policy_is_not_hidden_in_tfvars() -> None:
    environment = _terraform_sources(Path("infra/terraform/aws/environments/dev"))
    example = Path("infra/terraform/aws/environments/dev/terraform.tfvars.example").read_text(
        encoding="utf-8"
    )

    assert 'databases = ["curated_arxiv"]' in environment
    assert 'databases = ["curated_github"]' in environment
    assert 'databases = ["analytics_engineering"]' in environment
    assert 'prefixes  = ["arxiv"]' in environment
    assert 'prefixes  = ["github"]' in environment
    assert 'prefixes  = ["tables"]' in environment
    assert "notification_destinations" not in environment
    assert "arxiv_inspector_access" not in example
    assert "workload_data_access" not in example
    assert "alert_email" not in example
    assert "slack_channel" not in example


def test_catalog_admin_mutates_contract_tiers_but_only_reads_analytics_metadata() -> None:
    catalog = Path("infra/terraform/aws/modules/identity/catalog.tf").read_text(encoding="utf-8")
    identity = Path("infra/terraform/aws/modules/identity/main.tf").read_text(encoding="utf-8")

    assert 'sid = "ApplyContractManagedCatalog"' in catalog
    assert 'sid = "ReadLakehouseCatalog"' in catalog
    assert "local.analytics_table_arns" in catalog
    assert 'sid       = "ReadAnalyticsMetadata"' in catalog
    assert 'actions   = ["s3:GetObject"]' in catalog
    assert '"${var.bucket_arns.analytics}/tables/*/metadata/*"' in identity
    assert "local.ingestion_metadata_object_arns" in catalog
    assert '"${var.bucket_arns.landing}/*/*/tables/*/metadata/*"' in identity
    assert '"${var.bucket_arns.curated}/*/tables/*/metadata/*"' in identity
    assert "local.analytics_object_arns_by_workload" not in catalog


def test_emr_artifact_access_uses_only_exact_object_actions() -> None:
    emr = Path("infra/terraform/aws/modules/identity/emr.tf").read_text(encoding="utf-8")

    assert 'sid       = "ReadJobArtifacts"' in emr
    assert 'sid = "PublishImmutableJobArtifacts"' in emr
    assert 'resources = ["${var.bucket_arns.artifacts}/emr/jobs/*"]' in emr
    assert "GetJobArtifactBucketLocation" not in emr
    assert "ListJobArtifacts" not in emr
    assert emr.count("s3:ListMultipartUploadParts") == 1
