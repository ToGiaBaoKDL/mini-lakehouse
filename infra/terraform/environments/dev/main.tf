data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  project                     = "tgbao"
  environment                 = "dev"
  name_prefix                 = "${local.project}-${local.environment}"
  catalog_alias               = "glue"
  parameter_prefix            = "/lakehouse/${local.environment}"
  athena_workgroup            = "primary"
  athena_query_results_prefix = "athena/primary"
  athena_workgroup_arn        = "arn:${data.aws_partition.current.partition}:athena:${var.aws_region}:${data.aws_caller_identity.current.account_id}:workgroup/${local.athena_workgroup}"
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

module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = "lakehouse/${local.environment}"
  groups = {
    "airflow/connections" = [
      "slack_api_default",
      "smtp_default",
    ]
    "ocr/providers" = [
      "kaggle",
      "modal",
    ]
  }
  tags = local.tags
}

module "parameters" {
  source      = "../../modules/parameters"
  path_prefix = local.parameter_prefix
  values = {
    "storage/landing_uri"         = "s3://${module.storage.bucket_names.landing}"
    "storage/curated_uri"         = "s3://${module.storage.bucket_names.curated}"
    "storage/analytics_uri"       = "s3://${module.storage.bucket_names.analytics}"
    "storage/artifacts_uri"       = "s3://${module.storage.bucket_names.artifacts}"
    "storage/query_results_uri"   = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_query_results_prefix}"
    "catalog/name"                = local.catalog_alias
    "emr/application_id"          = module.emr_serverless.application_id
    "emr/execution_role_arn"      = module.identity.emr_runtime_role_arn
    "emr/artifacts_uri"           = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
    "notifications/alert_email"   = var.alert_email
    "notifications/slack_channel" = var.slack_channel
  }
  tags = local.tags
}

locals {
  parameter_arn_prefix   = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.parameter_prefix}"
  emr_code_parameter_arn = "${local.parameter_arn_prefix}/emr/code_uri"
  parameter_arns = {
    airflow = toset([
      "${local.parameter_arn_prefix}/storage/landing_uri",
      "${local.parameter_arn_prefix}/catalog/name",
      "${local.parameter_arn_prefix}/emr/application_id",
      "${local.parameter_arn_prefix}/emr/execution_role_arn",
      "${local.parameter_arn_prefix}/notifications/alert_email",
      "${local.parameter_arn_prefix}/notifications/slack_channel",
      local.emr_code_parameter_arn,
    ])
    catalog_admin = toset([
      "${local.parameter_arn_prefix}/storage/landing_uri",
      "${local.parameter_arn_prefix}/storage/curated_uri",
      "${local.parameter_arn_prefix}/catalog/name",
    ])
    emr_deployer = toset([
      local.emr_code_parameter_arn,
    ])
    dbt_transformer = toset([
      "${local.parameter_arn_prefix}/storage/analytics_uri",
      "${local.parameter_arn_prefix}/storage/query_results_uri",
    ])
    document_inspector = toset([
      "${local.parameter_arn_prefix}/storage/curated_uri",
      "${local.parameter_arn_prefix}/storage/query_results_uri",
    ])
    lightdash_reader = toset([
      "${local.parameter_arn_prefix}/storage/analytics_uri",
      "${local.parameter_arn_prefix}/storage/query_results_uri",
    ])
  }
}

module "identity" {
  source                      = "../../modules/identity"
  name_prefix                 = local.name_prefix
  account_id                  = data.aws_caller_identity.current.account_id
  aws_region                  = var.aws_region
  trusted_principal_arns      = var.trusted_principal_arns
  bucket_arns                 = module.storage.bucket_arns
  parameter_arns              = local.parameter_arns
  kms_key_arn                 = module.storage.kms_key_arn
  emr_application_arn         = module.emr_serverless.application_arn
  athena_workgroup_arn        = local.athena_workgroup_arn
  athena_query_results_prefix = local.athena_query_results_prefix
  airflow_connection_secret_arns = [
    module.secrets.secret_arns["airflow/connections/slack_api_default"],
    module.secrets.secret_arns["airflow/connections/smtp_default"],
  ]
  document_inspector_database_names = var.document_inspector_database_names
  tags                              = local.tags
}
