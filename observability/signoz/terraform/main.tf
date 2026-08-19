provider "signoz" {
  endpoint     = var.signoz_endpoint
  access_token = var.signoz_access_token
}

locals {
  alert_channels = length(var.signoz_alert_channels) > 0 ? var.signoz_alert_channels : null
}
