output "airflow_role_arn" {
  description = "IAM role assumed by the self-hosted Airflow runtime."
  value       = aws_iam_role.airflow.arn
}

output "emr_runtime_role_arn" {
  description = "Execution role assumed by EMR Serverless jobs."
  value       = aws_iam_role.emr_runtime.arn
}

output "emr_deployer_role_arn" {
  description = "IAM role allowed to publish immutable EMR job releases."
  value       = aws_iam_role.emr_deployer.arn
}

output "catalog_admin_role_arn" {
  description = "IAM role allowed to apply contract-owned Glue and Iceberg metadata."
  value       = aws_iam_role.catalog_admin.arn
}

output "document_inspector_role_arn" {
  description = "Read-only data access role for Document Inspector."
  value       = aws_iam_role.document_inspector.arn
}

output "dbt_transformer_role_arn" {
  description = "IAM role used by dbt to read curated data and manage analytics."
  value       = aws_iam_role.dbt_transformer.arn
}
