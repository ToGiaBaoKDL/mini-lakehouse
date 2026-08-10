variable "name" {
  type        = string
  description = "Name of the EMR Serverless Spark application."

  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "name must not be empty."
  }
}

variable "release_label" {
  type        = string
  description = "Pinned EMR release label for the Spark runtime."
  default     = "emr-7.13.0"

  validation {
    condition     = can(regex("^emr-[0-9]+\\.[0-9]+\\.[0-9]+$", var.release_label))
    error_message = "release_label must use the emr-X.Y.Z format."
  }
}

variable "catalog_alias" {
  type        = string
  description = "Spark SQL catalog alias configured for Iceberg and Glue."
  default     = "glue"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]*$", var.catalog_alias))
    error_message = "catalog_alias must be a valid Spark SQL identifier."
  }
}

variable "idle_timeout_minutes" {
  type        = number
  description = "Idle period before EMR Serverless automatically stops workers."
  default     = 15

  validation {
    condition     = var.idle_timeout_minutes >= 1 && floor(var.idle_timeout_minutes) == var.idle_timeout_minutes
    error_message = "idle_timeout_minutes must be a positive whole number."
  }
}

variable "maximum_capacity" {
  type = object({
    cpu    = string
    memory = string
    disk   = string
  })
  description = "Application-level maximum CPU, memory, and disk capacity."

  validation {
    condition = alltrue([
      for value in values(var.maximum_capacity) : length(trimspace(value)) > 0
    ])
    error_message = "maximum_capacity values must not be empty."
  }
}

variable "scheduler" {
  type = object({
    max_concurrent_runs   = number
    queue_timeout_minutes = number
  })
  description = "Application-level concurrency and queue timeout controls."

  validation {
    condition = (
      var.scheduler.max_concurrent_runs >= 1 &&
      floor(var.scheduler.max_concurrent_runs) == var.scheduler.max_concurrent_runs &&
      var.scheduler.queue_timeout_minutes >= 1 &&
      floor(var.scheduler.queue_timeout_minutes) == var.scheduler.queue_timeout_minutes
    )
    error_message = "Scheduler concurrency and queue timeout must be positive whole numbers."
  }
}

variable "spark_properties" {
  type        = map(string)
  description = "Additional Spark defaults merged over the module's Iceberg-safe defaults."
  default     = {}
}

variable "subnet_ids" {
  type        = set(string)
  description = "Subnet identifiers available to EMR Serverless workers."

  validation {
    condition     = length(var.subnet_ids) >= 2 && length(var.subnet_ids) <= 16
    error_message = "subnet_ids must contain between 2 and 16 unique subnets."
  }
}

variable "security_group_ids" {
  type        = set(string)
  description = "Security group identifiers attached to EMR Serverless workers."

  validation {
    condition     = length(var.security_group_ids) >= 1 && length(var.security_group_ids) <= 5
    error_message = "security_group_ids must contain between 1 and 5 unique security groups."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the EMR Serverless application."
  default     = {}
}
