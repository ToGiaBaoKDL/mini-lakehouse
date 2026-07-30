variable "name_prefix" {
  type        = string
  description = "Stable project prefix used in bucket and KMS names."
}

variable "bucket_tiers" {
  type        = set(string)
  description = "Logical storage tiers provisioned as independently named S3 buckets."

  validation {
    condition = length(var.bucket_tiers) > 0 && alltrue([
      for tier in var.bucket_tiers :
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", tier))
    ])
    error_message = "bucket_tiers must contain normalized lowercase S3 name components."
  }
}

variable "versioned_tiers" {
  type        = set(string)
  description = "Storage tiers that retain S3 object versions."
  default     = []

  validation {
    condition     = length(setsubtract(var.versioned_tiers, var.bucket_tiers)) == 0
    error_message = "versioned_tiers must be a subset of bucket_tiers."
  }
}

variable "expiration_days" {
  type        = map(number)
  description = "Optional object expiration by bucket tier."
  default     = {}

  validation {
    condition = length(setsubtract(toset(keys(var.expiration_days)), var.bucket_tiers)) == 0 && alltrue([
      for days in values(var.expiration_days) : days >= 1 && floor(days) == days
    ])
    error_message = "expiration_days keys must exist in bucket_tiers and values must be whole days."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment included in resource names and tags."
}

variable "force_destroy" {
  type        = bool
  description = "Whether Terraform may delete non-empty data buckets."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to storage and encryption resources."
  default     = {}
}
