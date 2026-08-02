variable "owner" {
  type        = string
  description = "Tailscale user identity allowed to administer and access the dev services host."

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+$", var.owner))
    error_message = "owner must be the email address of a Tailscale user."
  }
}
