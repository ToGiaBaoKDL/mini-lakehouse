variable "name_prefix" {
  type        = string
  description = "Environment-qualified prefix used for ECR repository names."
}

variable "repositories" {
  type        = set(string)
  description = "Logical deployable service names."

  validation {
    condition = length(var.repositories) > 0 && alltrue([
      for repository in var.repositories :
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", repository))
    ])
    error_message = "repositories must contain normalized lowercase names."
  }
}

variable "retained_image_count" {
  type        = number
  description = "Number of immutable releases retained per repository."
  default     = 20

  validation {
    condition = (
      var.retained_image_count >= 1 &&
      floor(var.retained_image_count) == var.retained_image_count
    )
    error_message = "retained_image_count must be a positive whole number."
  }
}

variable "force_delete" {
  type        = bool
  description = "Whether Terraform may delete repositories containing images."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to container repositories."
  default     = {}
}
