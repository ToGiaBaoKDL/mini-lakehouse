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

output "arxiv_inspector_role_arn" {
  description = "IAM role used by ArXiv Inspector."
  value       = module.identity.arxiv_inspector_role_arn
}

output "dbt_transformer_role_arn" {
  description = "IAM role used by dbt analytics transformations."
  value       = module.identity.dbt_transformer_role_arn
}

output "container_repository_urls" {
  description = "Immutable ECR repositories keyed by local deployable service."
  value       = module.container_registry.repository_urls
}

output "image_publisher_role_arn" {
  description = "IAM role used to publish and pull local service images."
  value       = module.identity.image_publisher_role_arn
}

output "ocr_worker_role_arn" {
  description = "IAM role used by the local OCR task container."
  value       = module.identity.ocr_worker_role_arn
}
