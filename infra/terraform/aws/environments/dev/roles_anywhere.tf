locals {
  external_workload_roles = merge({
    airflow           = module.identity.airflow_role_arn
    arxiv_lens        = module.identity.arxiv_lens_role_arn
    lightdash         = module.identity.lightdash_role_arn
    metadata_postgres = module.identity.metadata_postgres_role_arn
    services_deployer = module.identity.services_deployer_role_arn
    t0_trading        = module.identity.t0_trading_role_arn
  }, module.identity.dbt_domain_role_arns)
}

resource "aws_rolesanywhere_trust_anchor" "workloads" {
  name    = "${local.name_prefix}-external-workloads"
  enabled = true

  source {
    source_type = "CERTIFICATE_BUNDLE"
    source_data {
      x509_certificate_data = file(pathexpand(var.roles_anywhere_ca_certificate_path))
    }
  }

  tags = local.tags
}

resource "aws_rolesanywhere_profile" "workload" {
  for_each = local.external_workload_roles

  name             = "${local.name_prefix}-${replace(each.key, "_", "-")}"
  enabled          = true
  duration_seconds = 3600
  role_arns        = [each.value]

  tags = local.tags
}
