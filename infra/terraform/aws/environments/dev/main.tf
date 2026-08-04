data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  project          = "tgbao"
  environment      = "dev"
  aws_region       = "ap-southeast-1"
  name_prefix      = "${local.project}-${local.environment}"
  parameter_prefix = "/lakehouse/${local.environment}"
  athena_workgroup = "primary"
  github_repository = {
    owner    = "ToGiaBaoKDL"
    owner_id = "136962009"
    name     = "mini-lakehouse"
    id       = "1313563456"
  }
  github_environment_subject = "repo:${local.github_repository.owner}@${local.github_repository.owner_id}/${local.github_repository.name}@${local.github_repository.id}:environment:${local.environment}"
  bucket_names = {
    landing         = "${local.name_prefix}-landing-cy8j1c"
    curated         = "${local.name_prefix}-curated-za7rju"
    analytics       = "${local.name_prefix}-analytics-vt77zs"
    artifacts       = "${local.name_prefix}-artifacts-uhiv2y"
    logs            = "${local.name_prefix}-logs-71k0oc"
    "query-results" = "${local.name_prefix}-query-results-q2034x"
  }
  athena_workload_prefixes = {
    dbt_transformer = "dbt"
    arxiv_inspector = "arxiv-inspector"
  }
  workload_data_access = {
    curated = {
      arxiv_inspector = {
        databases = ["curated_arxiv"]
        prefixes  = ["arxiv"]
      }
      dbt_transformer = {
        databases = ["curated_github"]
        prefixes  = ["github"]
      }
      ocr_worker = {
        databases = ["curated_arxiv"]
        prefixes  = ["arxiv"]
      }
    }
    analytics = {
      dbt_transformer = {
        databases = ["analytics_engineering"]
        prefixes  = ["tables"]
      }
    }
  }
  athena_workgroup_arn = "arn:${data.aws_partition.current.partition}:athena:${local.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${local.athena_workgroup}"
  tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }
}

module "storage" {
  source       = "../../modules/storage"
  name_prefix  = local.name_prefix
  bucket_names = local.bucket_names
  expiration_days = {
    "query-results" = 7
    logs            = 30
  }
  force_destroy = true
  tags          = local.tags
}

module "container_registry" {
  source               = "../../modules/container_registry"
  name_prefix          = local.name_prefix
  repositories         = ["airflow", "arxiv-inspector", "dbt-task", "ocr-worker"]
  retained_image_count = 20
  force_delete         = true
  tags                 = local.tags
}

module "emr_serverless" {
  source               = "../../modules/emr_serverless"
  name                 = "${local.name_prefix}-spark"
  idle_timeout_minutes = 15
  maximum_capacity = {
    cpu    = "16 vCPU"
    memory = "64 GB"
    disk   = "200 GB"
  }
  scheduler = {
    max_concurrent_runs   = 2
    queue_timeout_minutes = 60
  }
  tags = local.tags
}

module "identity" {
  source                          = "../../modules/identity"
  name_prefix                     = local.name_prefix
  account_id                      = data.aws_caller_identity.current.account_id
  aws_region                      = local.aws_region
  catalog_admin_principal_arns    = var.catalog_admin_principal_arns
  github_oidc_provider_arn        = aws_iam_openid_connect_provider.github.arn
  github_environment_subject      = local.github_environment_subject
  roles_anywhere_trust_anchor_arn = aws_rolesanywhere_trust_anchor.workloads.arn
  parameter_arns                  = local.parameter_arns
  kms_key_arn                     = module.storage.kms_key_arn
  emr_application_arn             = module.emr_serverless.application_arn
  athena_workgroup_arn            = local.athena_workgroup_arn
  athena_result_prefixes          = local.athena_workload_prefixes
  bucket_arns = {
    landing       = module.storage.bucket_arns.landing
    curated       = module.storage.bucket_arns.curated
    analytics     = module.storage.bucket_arns.analytics
    artifacts     = module.storage.bucket_arns.artifacts
    logs          = module.storage.bucket_arns.logs
    query_results = module.storage.bucket_arns["query-results"]
  }
  airflow_secret_arns = toset(concat(
    [for secret in aws_secretsmanager_secret.airflow : secret.arn],
    [aws_secretsmanager_secret.metadata_postgres["airflow"].arn],
  ))
  metadata_postgres_secret_arns = toset([
    for secret in aws_secretsmanager_secret.metadata_postgres : secret.arn
  ])
  ocr_secret_arns = toset([for secret in aws_secretsmanager_secret.ocr : secret.arn])
  services_deployer_secret_arns = toset([
    aws_secretsmanager_secret.cloudflare_tunnel.arn,
  ])
  container_repository_arns = toset(values(module.container_registry.repository_arns))
  workload_data_access      = local.workload_data_access
  tags                      = local.tags
}
