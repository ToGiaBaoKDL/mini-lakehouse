resource "aws_ssm_parameter" "this" {
  for_each = var.values

  name  = "${trimsuffix(var.path_prefix, "/")}/${each.key}"
  type  = "String"
  value = each.value
  tier  = "Standard"
  tags  = var.tags
}
