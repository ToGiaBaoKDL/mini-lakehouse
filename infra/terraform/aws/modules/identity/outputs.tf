output "airflow_role_arn" {
  description = "IAM role assumed by the self-hosted Airflow runtime."
  value       = aws_iam_role.airflow.arn
}

output "metadata_postgres_role_arn" {
  description = "IAM role used to materialize shared metadata PostgreSQL secrets."
  value       = aws_iam_role.metadata_postgres.arn
}

output "emr_runtime_role_arn" {
  description = "Execution role assumed by EMR Serverless jobs."
  value       = aws_iam_role.emr_runtime.arn
}

output "catalog_admin_role_arn" {
  description = "IAM role allowed to apply contract-owned Glue and Iceberg metadata."
  value       = aws_iam_role.catalog_admin.arn
}

output "services_deployer_role_arn" {
  description = "IAM role used to deploy immutable self-hosted services."
  value       = aws_iam_role.services_deployer.arn
}

output "arxiv_lens_role_arn" {
  description = "Read-only data access role for ArXiv Lens."
  value       = aws_iam_role.arxiv_lens.arn
}

output "lightdash_role_arn" {
  description = "Read-only analytics and owned-storage role for Lightdash."
  value       = aws_iam_role.lightdash.arn
}

output "lakehouse_ingest_role_arn" {
  description = "IAM role used by bounded OCI source-capture tasks."
  value       = aws_iam_role.lakehouse_ingest.arn
}

output "t0_trading_role_arn" {
  description = "IAM role used by the OCI T0 trading runtime."
  value       = aws_iam_role.t0_trading.arn
}

output "dbt_domain_role_arns" {
  description = "Least-privilege IAM roles keyed by independently owned dbt domain workload."
  value       = { for workload, role in aws_iam_role.dbt_domain : workload => role.arn }
}

output "github_image_publisher_role_arn" {
  description = "GitHub Actions role allowed to publish immutable component images."
  value       = aws_iam_role.github_image_publisher.arn
}

output "github_emr_publisher_role_arn" {
  description = "GitHub Actions role allowed to publish immutable EMR releases."
  value       = aws_iam_role.github_emr_publisher.arn
}

output "github_docs_deployer_role_arn" {
  description = "GitHub Actions role allowed to read the Cloudflare documentation CI token."
  value       = aws_iam_role.github_docs_deployer.arn
}

output "github_lightdash_deployer_role_arn" {
  description = "GitHub Actions role allowed to read the Lightdash CI token."
  value       = aws_iam_role.github_lightdash_deployer.arn
}

output "github_signoz_deployer_role_arn" {
  description = "GitHub Actions role allowed to read the SigNoz CI token."
  value       = aws_iam_role.github_signoz_deployer.arn
}
