variable "name_prefix" {
  type        = string
  description = "Environment-qualified prefix used for the KMS alias and description."
}

variable "bucket_names" {
  type        = map(string)
  description = "Globally unique S3 bucket names keyed by logical storage tier."

  validation {
    condition     = length(var.bucket_names) > 0 && length(toset(values(var.bucket_names))) == length(var.bucket_names)
    error_message = "bucket_names must contain at least one unique bucket name."
  }
}

variable "expiration_days" {
  type        = map(number)
  description = "Optional object expiration by bucket tier."
  default     = {}

  validation {
    condition = length(setsubtract(toset(keys(var.expiration_days)), toset(keys(var.bucket_names)))) == 0 && alltrue([
      for days in values(var.expiration_days) : days >= 1 && floor(days) == days
    ])
    error_message = "expiration_days keys must exist in bucket_names and values must be whole days."
  }
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
