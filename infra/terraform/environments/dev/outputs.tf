output "emr_artifacts_uri" {
  value = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
}

output "emr_code_parameter_name" {
  value = "${local.parameter_prefix}/emr/code_uri"
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
