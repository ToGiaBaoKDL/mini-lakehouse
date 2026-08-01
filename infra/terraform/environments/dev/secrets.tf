locals {
  airflow_connection_names = toset([
    "slack_api_default",
    "smtp_default",
  ])
  airflow_secret_names = merge(
    {
      bootstrap = "lakehouse/${local.environment}/airflow/bootstrap"
    },
    {
      for connection in local.airflow_connection_names :
      connection => "lakehouse/${local.environment}/airflow/connections/${connection}"
    },
  )
}

resource "aws_secretsmanager_secret" "airflow" {
  for_each = local.airflow_secret_names

  name = each.value
  description = each.key == "bootstrap" ? (
    "Bootstrap credentials for the self-hosted Airflow runtime."
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
