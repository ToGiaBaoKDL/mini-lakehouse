locals {
  runtime_parameters = {
    "storage/landing_uri"               = "s3://${module.storage.bucket_names.landing}"
    "storage/curated_uri"               = "s3://${module.storage.bucket_names.curated}"
    "storage/analytics_uri"             = "s3://${module.storage.bucket_names.analytics}"
    "athena/dbt_output_uri"             = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_workload_prefixes.dbt_transformer}"
    "athena/arxiv_inspector_output_uri" = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_workload_prefixes.arxiv_inspector}"
    "airflow/remote_log_uri"            = "s3://${module.storage.bucket_names.logs}/airflow/task-logs"
    "emr/application_id"                = module.emr_serverless.application_id
    "emr/execution_role_arn"            = module.identity.emr_runtime_role_arn
    "ocr/providers/kaggle_secret_id"    = aws_secretsmanager_secret.ocr["kaggle"].name
    "ocr/providers/modal_secret_id"     = aws_secretsmanager_secret.ocr["modal"].name
  }
  parameter_names_by_workload = {
    airflow = [
      "storage/landing_uri",
      "storage/analytics_uri",
      "athena/dbt_output_uri",
      "emr/application_id",
      "emr/execution_role_arn",
      "airflow/remote_log_uri",
      "emr/code_uri",
    ]
    catalog_admin = [
      "storage/landing_uri",
      "storage/curated_uri",
    ]
    emr_deployer = [
      "emr/code_uri",
    ]
    dbt_transformer = [
      "storage/analytics_uri",
      "athena/dbt_output_uri",
    ]
    arxiv_inspector = [
      "storage/curated_uri",
      "athena/arxiv_inspector_output_uri",
    ]
    ocr_worker = [
      "storage/curated_uri",
      "ocr/providers/kaggle_secret_id",
      "ocr/providers/modal_secret_id",
    ]
  }
  parameter_arn_prefix = "arn:${data.aws_partition.current.partition}:ssm:${local.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.parameter_prefix}"
  external_parameter_names = toset([
    "emr/code_uri",
  ])
  managed_parameter_names = toset(keys(local.runtime_parameters))
  granted_parameter_names = toset(flatten([
    for names in values(local.parameter_names_by_workload) : tolist(names)
  ]))
  parameter_arns = {
    for workload, names in local.parameter_names_by_workload :
    workload => toset([
      for name in names : "${local.parameter_arn_prefix}/${name}"
    ])
  }
}

check "runtime_parameter_grants" {
  assert {
    condition = (
      length(setsubtract(
        local.granted_parameter_names,
        setunion(local.managed_parameter_names, local.external_parameter_names),
      )) == 0 &&
      length(setsubtract(local.managed_parameter_names, local.granted_parameter_names)) == 0
    )
    error_message = "Runtime parameter grants must reference known parameters, and every managed parameter needs a consumer."
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
