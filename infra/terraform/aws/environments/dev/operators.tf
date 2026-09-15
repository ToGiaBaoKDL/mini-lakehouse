resource "aws_iam_user_policy_attachment" "billing" {
  for_each = var.billing_user_names

  user       = each.value
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/job-function/Billing"
}
