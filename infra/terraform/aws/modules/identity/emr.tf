resource "aws_iam_role" "emr_runtime" {
  name               = "${var.name_prefix}-emr-runtime"
  assume_role_policy = data.aws_iam_policy_document.emr_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "emr_runtime" {
  statement {
    sid       = "ListIngestionBuckets"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = local.ingestion_bucket_arns
  }
  statement {
    sid = "ReadWriteIngestionObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = local.ingestion_object_arns
  }
  statement {
    sid       = "ListJobArtifacts"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [var.bucket_arns.artifacts]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["emr/jobs/*"]
    }
  }
  statement {
    sid       = "ReadJobArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arns.artifacts}/emr/jobs/*"]
  }
  statement {
    sid       = "ListGlueDatabases"
    actions   = ["glue:GetDatabases"]
    resources = [local.glue_catalog_arn]
  }
  statement {
    sid = "CommitContractManagedTables"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.landing_database_arns,
      local.curated_database_arns,
      local.landing_table_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "emr_runtime" {
  role   = aws_iam_role.emr_runtime.id
  policy = data.aws_iam_policy_document.emr_runtime.json
}

data "aws_iam_policy_document" "emr_publisher" {
  statement {
    sid       = "ListJobArtifacts"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [var.bucket_arns.artifacts]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["emr/jobs/*"]
    }
  }
  statement {
    sid = "PublishImmutableJobArtifacts"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.artifacts}/emr/jobs/*"]
  }
  statement {
    sid       = "PublishCurrentReleasePointer"
    actions   = ["ssm:PutParameter"]
    resources = var.parameter_arns.emr_publisher
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}
