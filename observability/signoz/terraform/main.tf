provider "signoz" {
  endpoint = var.signoz_endpoint
}

locals {
  alert_channels = length(var.signoz_alert_channels) > 0 ? var.signoz_alert_channels : null
  use_policy     = local.alert_channels == null ? true : null
}
