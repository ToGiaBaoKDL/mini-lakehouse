locals {
  airflow_connection_names = toset([
    "slack_api_default",
    "smtp_default",
  ])
}

resource "aws_secretsmanager_secret" "airflow_connection" {
  for_each = local.airflow_connection_names

  name                    = "lakehouse/${local.environment}/airflow/connections/${each.value}"
  recovery_window_in_days = 7
  tags                    = local.tags
}
