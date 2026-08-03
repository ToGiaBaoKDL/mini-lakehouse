locals {
  external_workload_roles = {
    airflow           = module.identity.airflow_role_arn
    arxiv_inspector   = module.identity.arxiv_inspector_role_arn
    metadata_postgres = module.identity.metadata_postgres_role_arn
    services_deployer = module.identity.services_deployer_role_arn
    dbt_transformer   = module.identity.dbt_transformer_role_arn
    ocr_worker        = module.identity.ocr_worker_role_arn
  }
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
