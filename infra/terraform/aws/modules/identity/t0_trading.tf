resource "aws_iam_role" "t0_trading" {
  name               = "${var.name_prefix}-t0-trading"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["t0_trading"].json
  tags               = var.tags
}

locals {
  t0_trading_raw_prefixes = [
    "api/ssi_fastconnect_rest/raw",
    "stream/ssi_fastconnect_stream/raw",
    "rdbms/t0_trading/raw",
  ]
}

data "aws_iam_policy_document" "t0_trading" {
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.t0_trading
  }
  statement {
    sid       = "ReadOwnedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.t0_trading_secret_arns
  }
  statement {
    sid       = "GetLandingBucketLocation"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arns.landing]
  }
  statement {
    sid       = "ListOwnedRawObjects"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.landing]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = flatten([
        for prefix in local.t0_trading_raw_prefixes : [prefix, "${prefix}/*"]
      ])
    }
  }
  statement {
    sid = "PublishImmutableRawObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      for prefix in local.t0_trading_raw_prefixes : "${var.bucket_arns.landing}/${prefix}/*"
    ]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "t0_trading" {
  role   = aws_iam_role.t0_trading.id
  policy = data.aws_iam_policy_document.t0_trading.json
}
