variable "name_prefix" {
  type = string
}

variable "bucket_tiers" {
  type = set(string)
}

variable "versioned_tiers" {
  type    = set(string)
  default = []
}

variable "expiration_days" {
  type        = map(number)
  default     = {}
  description = "Optional object expiration by bucket tier."

  validation {
    condition     = alltrue([for days in values(var.expiration_days) : days >= 1])
    error_message = "Bucket expiration must be at least one day."
  }
}

variable "environment" {
  type = string
}

variable "force_destroy" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
