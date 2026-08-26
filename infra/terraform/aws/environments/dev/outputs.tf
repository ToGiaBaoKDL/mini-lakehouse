output "emr_artifacts_uri" {
  description = "Base S3 URI for immutable EMR job releases."
  value       = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
}

output "emr_code_parameter_name" {
  description = "SSM parameter updated by the EMR release publisher."
  value       = "${local.parameter_prefix}/emr/code_uri"
}

output "catalog_admin_role_arn" {
  description = "Human-assumable role allowed to apply and validate catalog contracts."
  value       = module.identity.catalog_admin_role_arn
}

output "github_ci_role_arns" {
  description = "Environment-scoped GitHub Actions roles keyed by release responsibility."
  value = {
    docs_deployer      = module.identity.github_docs_deployer_role_arn
    emr_publisher      = module.identity.github_emr_publisher_role_arn
    image_publisher    = module.identity.github_image_publisher_role_arn
    lightdash_deployer = module.identity.github_lightdash_deployer_role_arn
    signoz_deployer    = module.identity.github_signoz_deployer_role_arn
  }
}

output "cloudflare_docs_ci_secret_id" {
  description = "Secrets Manager identifier populated with the Cloudflare documentation deployment token."
  value       = aws_secretsmanager_secret.cloudflare_docs_ci.name
}

output "lightdash_ci_secret_id" {
  description = "Secrets Manager identifier populated with the Lightdash CI personal access token."
  value       = aws_secretsmanager_secret.lightdash_ci.name
}

output "signoz_ci_secret_id" {
  description = "Secrets Manager identifier populated with the SigNoz CI API access token."
  value       = aws_secretsmanager_secret.signoz_ci.name
}

output "container_repository_urls" {
  description = "Immutable ECR repositories keyed by deployable service."
  value       = module.container_registry.repository_urls
}

output "cloudflare_tunnel_secret_id" {
  description = "Secrets Manager identifier populated by the explicit Cloudflare token sync."
  value       = aws_secretsmanager_secret.cloudflare_tunnel.name
}

output "roles_anywhere_trust_anchor_arn" {
  description = "Trust anchor used by external workload certificates."
  value       = aws_rolesanywhere_trust_anchor.workloads.arn
}

output "roles_anywhere_workloads" {
  description = "Certificate identity, Roles Anywhere profile, and IAM role keyed by workload."
  value = {
    for workload, profile in aws_rolesanywhere_profile.workload : workload => {
      common_name = profile.name
      profile_arn = profile.arn
      role_arn    = local.external_workload_roles[workload]
    }
  }
}
