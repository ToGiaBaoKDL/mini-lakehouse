variable "name_prefix" {
  type        = string
  description = "Name prefix applied to the EMR network resources."

  validation {
    condition     = length(trimspace(var.name_prefix)) > 0
    error_message = "name_prefix must not be empty."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "IPv4 CIDR allocated to the EMR worker VPC."

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the EMR network resources."
  default     = {}
}
