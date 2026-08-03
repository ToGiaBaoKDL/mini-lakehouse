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
}

resource "aws_secretsmanager_secret" "metadata_postgres" {
  for_each = toset(["airflow", "bootstrap"])

  name                    = "lakehouse/${local.environment}/metadata-postgres/${each.key}"
  description             = each.key == "bootstrap" ? "Bootstrap credential for the shared metadata PostgreSQL server." : "Database credential owned by the Airflow metadata database."
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
  for_each = toset(["kaggle", "modal"])

  name                    = "lakehouse/${local.environment}/ocr/providers/${each.key}"
  description             = "${title(each.key)} credentials for remote OCR execution."
  recovery_window_in_days = 7
  tags                    = local.tags
}
