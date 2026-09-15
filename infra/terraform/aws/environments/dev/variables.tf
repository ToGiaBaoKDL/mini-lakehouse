variable "catalog_admin_principal_arns" {
  type        = set(string)
  description = "Existing IAM principal ARNs allowed to assume the contract catalog administrator role."

  validation {
    condition = length(var.catalog_admin_principal_arns) > 0 && alltrue([
      for arn in var.catalog_admin_principal_arns : startswith(arn, "arn:")
    ])
    error_message = "At least one valid catalog administrator principal ARN is required."
  }
}

variable "billing_user_names" {
  type        = set(string)
  description = "Existing IAM users allowed to manage Billing, costs, budgets, and payment methods."

  validation {
    condition = length(var.billing_user_names) > 0 && alltrue([
      for name in var.billing_user_names : name == trimspace(name) && name != ""
    ])
    error_message = "At least one non-empty billing IAM user name is required."
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
