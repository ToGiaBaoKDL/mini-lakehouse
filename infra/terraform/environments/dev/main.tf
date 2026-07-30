data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  project          = "tgbao"
  environment      = "dev"
  aws_region       = "ap-southeast-1"
  name_prefix      = "${local.project}-${local.environment}"
  catalog_alias    = "glue"
  parameter_prefix = "/lakehouse/${local.environment}"
  athena_workgroup = "primary"
  athena_workload_prefixes = {
    dbt_transformer    = "dbt"
    document_inspector = "document-inspector"
  }
  athena_workgroup_arn = "arn:${data.aws_partition.current.partition}:athena:${local.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${local.athena_workgroup}"
  tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }
}

module "storage" {
  source          = "../../modules/storage"
  name_prefix     = local.project
  environment     = local.environment
  bucket_tiers    = ["landing", "curated", "analytics", "artifacts", "query-results"]
  versioned_tiers = ["artifacts"]
  expiration_days = {
    "query-results" = 7
  }
  force_destroy = true
  tags          = local.tags
}

module "emr_serverless" {
  source               = "../../modules/emr_serverless"
  name                 = "${local.name_prefix}-spark"
  catalog_alias        = local.catalog_alias
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
  source                 = "../../modules/identity"
  name_prefix            = local.name_prefix
  account_id             = data.aws_caller_identity.current.account_id
  aws_region             = local.aws_region
  trusted_principal_arns = var.trusted_principal_arns
  bucket_arns            = module.storage.bucket_arns
  parameter_arns         = local.parameter_arns
  kms_key_arn            = module.storage.kms_key_arn
  emr_application_arn    = module.emr_serverless.application_arn
  athena_workgroup_arn   = local.athena_workgroup_arn
  athena_result_prefixes = local.athena_workload_prefixes
  airflow_connection_secret_arns = [
    for secret in aws_secretsmanager_secret.airflow_connection : secret.arn
  ]
  document_inspector_access = var.document_inspector_access
  tags                      = local.tags
}
