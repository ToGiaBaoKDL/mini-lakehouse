output "emr_artifacts_uri" {
  description = "Base S3 URI for immutable EMR job releases."
  value       = "s3://${module.storage.bucket_names.artifacts}/emr/jobs"
}

output "emr_code_parameter_name" {
  description = "SSM parameter updated by the EMR release publisher."
  value       = "${local.parameter_prefix}/emr/code_uri"
}

output "runtime_parameter_names" {
  description = "Managed runtime SSM parameter names keyed by their domain path."
  value       = { for name, parameter in aws_ssm_parameter.runtime : name => parameter.name }
}

output "operator_role_arns" {
  description = "Human or CI operator roles keyed by responsibility."
  value = {
    catalog_admin   = module.identity.catalog_admin_role_arn
    emr_deployer    = module.identity.emr_deployer_role_arn
    image_publisher = module.identity.image_publisher_role_arn
  }
}

output "container_repository_urls" {
  description = "Immutable ECR repositories keyed by deployable service."
  value       = module.container_registry.repository_urls
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
