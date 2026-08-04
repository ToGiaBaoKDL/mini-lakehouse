variable "account_id" {
  type        = string
  description = "Cloudflare account that owns the dev tunnel and Access applications."

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be a 32-character Cloudflare account ID."
  }
}

variable "zone_id" {
  type        = string
  description = "Cloudflare zone ID for tgblab.io.vn."

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.zone_id))
    error_message = "zone_id must be a 32-character Cloudflare zone ID."
  }
}

variable "access_allowed_emails" {
  type        = set(string)
  description = "User email addresses allowed through Cloudflare Access."

  validation {
    condition = length(var.access_allowed_emails) > 0 && alltrue([
      for email in var.access_allowed_emails : can(regex("^[^@[:space:]]+@[^@[:space:]]+$", email))
    ])
    error_message = "access_allowed_emails must contain at least one valid email address."
  }
}
