variable "signoz_endpoint" {
  description = "Root URL of the SigNoz UI/API. Reached over Tailscale (http://tgbao-dev-services:8082) or locally (http://127.0.0.1:8082)."
  type        = string
  default     = "http://127.0.0.1:8082"
}

variable "signoz_access_token" {
  description = "SigNoz API access token of a service account. Prefer the SIGNOZ_ACCESS_TOKEN environment variable so the secret stays out of configuration and state."
  type        = string
  sensitive   = true
  default     = null
}

variable "signoz_alert_channels" {
  description = "Names of notification channels configured once in the SigNoz UI (Settings → Alert Channels). Defaults to platform-alerts; when empty, thresholds rely on the UI default route."
  type        = list(string)
  default     = ["platform-alerts"]
}
