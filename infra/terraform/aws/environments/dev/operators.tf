resource "aws_iam_user_policy_attachment" "billing_read_only" {
  for_each = var.billing_read_only_user_names

  user       = each.value
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AWSBillingReadOnlyAccess"
}
