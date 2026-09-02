resource "aws_iam_role" "t0_trading" {
  name               = "${var.name_prefix}-t0-trading"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["t0_trading"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "t0_trading" {
  statement {
    sid       = "ReadOwnedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.t0_trading_secret_arn]
  }
  statement {
    sid = "PublishImmutableRestCaptures"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.landing}/api/ssi_fastconnect_rest/raw/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "t0_trading" {
  role   = aws_iam_role.t0_trading.id
  policy = data.aws_iam_policy_document.t0_trading.json
}
