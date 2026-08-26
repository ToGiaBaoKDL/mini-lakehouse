locals {
  airflow_connection_names = toset([
    "slack_api_default",
    "smtp_default",
  ])
  airflow_secret_names = merge(
    {
      runtime = "lakehouse/${local.environment}/airflow/runtime"
    },
    {
      for connection in local.airflow_connection_names :
      connection => "lakehouse/${local.environment}/airflow/connections/${connection}"
    },
  )
  metadata_postgres_secret_descriptions = {
    airflow    = "Database credential owned by the Airflow metadata database."
    bootstrap  = "Bootstrap credential for the shared metadata PostgreSQL server."
    lightdash  = "Database credential owned by the Lightdash metadata database."
    pg_monitor = "Read-only pg_monitor credential for host metrics collection."
  }
}

resource "aws_secretsmanager_secret" "metadata_postgres" {
  for_each = local.metadata_postgres_secret_descriptions

  name                    = "lakehouse/${local.environment}/metadata-postgres/${each.key}"
  description             = each.value
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "lightdash" {
  name                    = "lakehouse/${local.environment}/lightdash/runtime"
  description             = "Stable encryption secret for the self-hosted Lightdash service."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "lightdash_ci" {
  name                    = "lakehouse/${local.environment}/lightdash/ci"
  description             = "Personal access token for protected Lightdash content deployments."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "signoz_ci" {
  name                    = "lakehouse/${local.environment}/signoz/ci"
  description             = "Service account API access token for SigNoz dashboard and alert deployments."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "airflow" {
  for_each = local.airflow_secret_names

  name = each.value
  description = each.key == "runtime" ? (
    "Runtime credentials for the self-hosted Airflow service."
    ) : (
    "Airflow connection ${each.key} for the ${local.environment} environment."
  )
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "ocr" {
  for_each = toset(["modal"])

  name                    = "lakehouse/${local.environment}/ocr/providers/${each.key}"
  description             = "${title(each.key)} credentials for remote OCR execution."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "cloudflare_tunnel" {
  name                    = "lakehouse/${local.environment}/cloudflare/tunnel-token"
  description             = "Connector token for the Cloudflare Tunnel running on the services host."
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret" "cloudflare_docs_ci" {
  name                    = "lakehouse/${local.environment}/cloudflare/docs-ci"
  description             = "Scoped Cloudflare API token for protected documentation deployments."
  recovery_window_in_days = 7
  tags                    = local.tags
}
