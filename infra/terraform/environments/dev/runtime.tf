locals {
  runtime_parameters = {
    "storage/landing_uri"                  = "s3://${module.storage.bucket_names.landing}"
    "storage/curated_uri"                  = "s3://${module.storage.bucket_names.curated}"
    "storage/analytics_uri"                = "s3://${module.storage.bucket_names.analytics}"
    "athena/dbt_output_uri"                = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_workload_prefixes.dbt_transformer}"
    "athena/document_inspector_output_uri" = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_workload_prefixes.document_inspector}"
    "catalog/name"                         = local.catalog_alias
    "emr/application_id"                   = module.emr_serverless.application_id
    "emr/execution_role_arn"               = module.identity.emr_runtime_role_arn
    "notifications/alert_email"            = var.alert_email
    "notifications/slack_channel"          = var.slack_channel
  }
  parameter_names_by_workload = {
    airflow = [
      "storage/landing_uri",
      "catalog/name",
      "emr/application_id",
      "emr/execution_role_arn",
      "notifications/alert_email",
      "notifications/slack_channel",
      "emr/code_uri",
    ]
    catalog_admin = [
      "storage/landing_uri",
      "storage/curated_uri",
      "catalog/name",
    ]
    emr_deployer = [
      "emr/code_uri",
    ]
    dbt_transformer = [
      "storage/analytics_uri",
      "athena/dbt_output_uri",
    ]
    document_inspector = [
      "storage/curated_uri",
      "athena/document_inspector_output_uri",
    ]
  }
  parameter_arn_prefix = "arn:${data.aws_partition.current.partition}:ssm:${local.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.parameter_prefix}"
  parameter_arns = {
    for workload, names in local.parameter_names_by_workload :
    workload => toset([
      for name in names : "${local.parameter_arn_prefix}/${name}"
    ])
  }
}

resource "aws_ssm_parameter" "runtime" {
  for_each = local.runtime_parameters

  name  = "${local.parameter_prefix}/${each.key}"
  type  = "String"
  value = each.value
  tier  = "Standard"
  tags  = local.tags
}
