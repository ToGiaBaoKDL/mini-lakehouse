variable "aws_region" {
  type        = string
  description = "AWS Region containing the Terraform state bucket."
  default     = "ap-southeast-1"
}

variable "aws_profile" {
  type        = string
  description = "Optional local AWS profile used to bootstrap remote state."
  default     = null
}

variable "name_prefix" {
  type        = string
  description = "Stable project prefix used in the globally unique state bucket name."
  default     = "tgbao"
}
