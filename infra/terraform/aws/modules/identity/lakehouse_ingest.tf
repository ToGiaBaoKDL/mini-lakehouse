resource "aws_iam_role" "lakehouse_ingest" {
  name               = "${var.name_prefix}-lakehouse-ingest"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["lakehouse_ingest"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "lakehouse_ingest" {
  statement {
    sid = "PublishImmutableCaptures"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [for prefix in var.lakehouse_ingest_prefixes :
      "${var.bucket_arns.landing}/${prefix}/*"
    ]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "lakehouse_ingest" {
  role   = aws_iam_role.lakehouse_ingest.id
  policy = data.aws_iam_policy_document.lakehouse_ingest.json
}
