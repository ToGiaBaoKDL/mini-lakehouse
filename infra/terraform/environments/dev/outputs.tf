output "landing_uri" {
  value = "s3://${module.storage.bucket_names.landing}"
}

output "curated_uri" {
  value = "s3://${module.storage.bucket_names.curated}"
}

output "analytics_uri" {
  value = "s3://${module.storage.bucket_names.analytics}"
}

output "artifacts_uri" {
  value = "s3://${module.storage.bucket_names.artifacts}"
}

output "athena_query_results_uri" {
  value = "s3://${module.storage.bucket_names["query-results"]}/${local.athena_query_results_prefix}"
}

output "emr_artifacts_uri" {
  value = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
}

output "emr_code_parameter_name" {
  value = "${local.parameter_prefix}/emr/code_uri"
}

output "runtime_parameter_prefix" {
  value = local.parameter_prefix
}

output "athena_workgroup" {
  value = local.athena_workgroup
}

output "emr_application_id" {
  value = module.emr_serverless.application_id
}

output "emr_execution_role_arn" {
  value = module.identity.emr_runtime_role_arn
}

output "emr_deployer_role_arn" {
  value = module.identity.emr_deployer_role_arn
}

output "airflow_role_arn" {
  value = module.identity.airflow_role_arn
}

output "catalog_admin_role_arn" {
  value = module.identity.catalog_admin_role_arn
}

output "document_inspector_role_arn" {
  value = module.identity.document_inspector_role_arn
}

output "dbt_transformer_role_arn" {
  value = module.identity.dbt_transformer_role_arn
}

output "lightdash_reader_role_arn" {
  value = module.identity.lightdash_reader_role_arn
}
