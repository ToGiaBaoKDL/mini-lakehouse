output "emr_artifacts_uri" {
  description = "Base S3 URI for immutable EMR job releases."
  value       = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
}

output "emr_code_parameter_name" {
  description = "SSM parameter updated by the EMR release publisher."
  value       = "${local.parameter_prefix}/emr/code_uri"
}

output "emr_deployer_role_arn" {
  description = "IAM role used to publish EMR job releases."
  value       = module.identity.emr_deployer_role_arn
}

output "airflow_role_arn" {
  description = "IAM role used by the self-hosted Airflow runtime."
  value       = module.identity.airflow_role_arn
}

output "catalog_admin_role_arn" {
  description = "IAM role used to apply catalog contracts."
  value       = module.identity.catalog_admin_role_arn
}

output "document_inspector_role_arn" {
  description = "IAM role used by Document Inspector."
  value       = module.identity.document_inspector_role_arn
}

output "dbt_transformer_role_arn" {
  description = "IAM role used by dbt analytics transformations."
  value       = module.identity.dbt_transformer_role_arn
}
