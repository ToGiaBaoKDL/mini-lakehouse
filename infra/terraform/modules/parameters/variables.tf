variable "path_prefix" {
  type        = string
  description = "Absolute Parameter Store path owned by this environment."

  validation {
    condition     = startswith(var.path_prefix, "/")
    error_message = "Parameter Store path_prefix must start with '/'."
  }
}

variable "values" {
  type        = map(string)
  description = "Non-secret runtime resource references."
}

variable "tags" {
  type    = map(string)
  default = {}
}
