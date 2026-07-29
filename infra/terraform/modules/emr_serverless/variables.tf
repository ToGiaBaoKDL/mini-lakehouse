variable "name" {
  type = string
}

variable "release_label" {
  type    = string
  default = "emr-7.13.0"
}

variable "catalog_alias" {
  type    = string
  default = "glue"
}

variable "idle_timeout_minutes" {
  type    = number
  default = 15
}

variable "maximum_capacity" {
  type = object({
    cpu    = string
    memory = string
    disk   = string
  })
}

variable "scheduler" {
  type = object({
    max_concurrent_runs   = number
    queue_timeout_minutes = number
  })
}

variable "spark_properties" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
