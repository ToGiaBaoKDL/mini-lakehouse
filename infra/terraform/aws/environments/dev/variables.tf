variable "operator_principal_arns" {
  type        = set(string)
  description = "Existing IAM principal ARNs allowed to assume human break-glass operator roles."

  validation {
    condition = length(var.operator_principal_arns) > 0 && alltrue([
      for arn in var.operator_principal_arns : startswith(arn, "arn:")
    ])
    error_message = "At least one valid operator principal ARN is required."
  }
}

variable "roles_anywhere_ca_certificate_path" {
  type        = string
  description = "Path to the public PEM CA certificate trusted for external workload identities."

  validation {
    condition = (
      startswith(var.roles_anywhere_ca_certificate_path, "/") ||
      startswith(var.roles_anywhere_ca_certificate_path, "~/")
    )
    error_message = "roles_anywhere_ca_certificate_path must be absolute or start with ~/."
  }
}
