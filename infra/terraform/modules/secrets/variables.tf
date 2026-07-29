variable "name_prefix" {
  type = string
}

variable "groups" {
  type        = map(set(string))
  description = "Secret containers grouped by owning service boundary."
}

variable "tags" {
  type    = map(string)
  default = {}
}
