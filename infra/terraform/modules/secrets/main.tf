locals {
  names = toset(flatten([
    for group, names in var.groups : [
      for name in names : "${group}/${name}"
    ]
  ]))
}

resource "aws_secretsmanager_secret" "this" {
  for_each                = local.names
  name                    = "${var.name_prefix}/${each.value}"
  recovery_window_in_days = 7
  tags                    = var.tags
}
