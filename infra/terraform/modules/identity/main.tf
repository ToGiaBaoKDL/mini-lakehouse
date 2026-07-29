data "aws_partition" "current" {}

data "aws_iam_policy_document" "operator_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.trusted_principal_arns
    }
  }
}

data "aws_iam_policy_document" "emr_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
  }
}

locals {
  glue_catalog_arn = "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:catalog"
  landing_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/landing_*",
  ]
  curated_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/curated_*",
  ]
  analytics_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/analytics_*",
  ]
  landing_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/landing_*/*",
  ]
  curated_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/curated_*/*",
  ]
  analytics_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/analytics_*/*",
  ]
  document_inspector_database_arns = [
    for database in var.document_inspector_database_names :
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/${database}"
  ]
  document_inspector_table_arns = [
    for database in var.document_inspector_database_names :
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/${database}/*"
  ]
  ingestion_bucket_arns = [
    var.bucket_arns.landing,
    var.bucket_arns.curated,
  ]
  ingestion_object_arns = [for arn in local.ingestion_bucket_arns : "${arn}/*"]
}

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

resource "aws_iam_role" "emr_deployer" {
  name               = "${var.name_prefix}-emr-deployer"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "emr_deployer" {
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
    resources = var.parameter_arns.emr_deployer
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "emr_deployer" {
  role   = aws_iam_role.emr_deployer.id
  policy = data.aws_iam_policy_document.emr_deployer.json
}

resource "aws_iam_role" "airflow" {
  name               = "${var.name_prefix}-airflow"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "airflow" {
  statement {
    sid = "OperateEmrApplication"
    actions = [
      "emr-serverless:CancelJobRun",
      "emr-serverless:GetApplication",
      "emr-serverless:GetDashboardForJobRun",
      "emr-serverless:GetJobRun",
      "emr-serverless:ListJobRuns",
      "emr-serverless:StartJobRun",
    ]
    resources = [var.emr_application_arn, "${var.emr_application_arn}/jobruns/*"]
  }
  statement {
    sid       = "PassEmrRuntimeRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.emr_runtime.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["emr-serverless.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.airflow
  }
  statement {
    sid       = "ReadAirflowConnections"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = var.airflow_connection_secret_arns
  }
}

resource "aws_iam_role_policy" "airflow" {
  role   = aws_iam_role.airflow.id
  policy = data.aws_iam_policy_document.airflow.json
}

resource "aws_iam_role" "catalog_admin" {
  name               = "${var.name_prefix}-catalog-admin"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "catalog_admin" {
  statement {
    sid = "ApplyLakehouseContracts"
    actions = [
      "glue:CreateDatabase",
      "glue:CreateTable",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:UpdateDatabase",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.landing_database_arns,
      local.curated_database_arns,
      local.analytics_database_arns,
      local.landing_table_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.catalog_admin
  }
  statement {
    sid       = "ListManagedDataBuckets"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = local.ingestion_bucket_arns
  }
  statement {
    sid       = "ManageIcebergMetadata"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = local.ingestion_object_arns
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "catalog_admin" {
  role   = aws_iam_role.catalog_admin.id
  policy = data.aws_iam_policy_document.catalog_admin.json
}

resource "aws_iam_role" "dbt_transformer" {
  name               = "${var.name_prefix}-dbt-transformer"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "dbt_transformer" {
  statement {
    sid = "RunAthenaQueries"
    actions = [
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
    ]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    sid = "ReadCuratedCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.curated_database_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid = "ManageAnalyticsCatalog"
    actions = [
      "glue:CreateTable",
      "glue:DeleteTable",
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.analytics_database_arns,
      local.analytics_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = var.parameter_arns.dbt_transformer
  }
  statement {
    sid     = "GetTransformerBucketLocations"
    actions = ["s3:GetBucketLocation"]
    resources = [
      var.bucket_arns.curated,
      var.bucket_arns.analytics,
      var.bucket_arns["query-results"],
    ]
  }
  statement {
    sid     = "ListTransformerDataBuckets"
    actions = ["s3:ListBucket"]
    resources = [
      var.bucket_arns.curated,
      var.bucket_arns.analytics,
    ]
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns["query-results"]]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_query_results_prefix,
        "${var.athena_query_results_prefix}/*",
      ]
    }
  }
  statement {
    sid     = "ReadTransformerObjects"
    actions = ["s3:GetObject"]
    resources = [
      "${var.bucket_arns.curated}/*",
      "${var.bucket_arns.analytics}/*",
      "${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*",
    ]
  }
  statement {
    sid = "ManageAnalyticsObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.analytics}/*"]
  }
  statement {
    sid       = "WriteQueryResults"
    actions   = ["s3:AbortMultipartUpload", "s3:PutObject"]
    resources = ["${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "dbt_transformer" {
  role   = aws_iam_role.dbt_transformer.id
  policy = data.aws_iam_policy_document.dbt_transformer.json
}

resource "aws_iam_role" "document_inspector" {
  name               = "${var.name_prefix}-document-inspector"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "document_inspector" {
  statement {
    sid = "RunAthenaQueries"
    actions = [
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
    ]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    sid = "ReadDocumentCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.document_inspector_database_arns,
      local.document_inspector_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.document_inspector
  }
  statement {
    sid       = "GetDocumentBucketLocations"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arns.curated, var.bucket_arns["query-results"]]
  }
  statement {
    sid       = "ListDocumentData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.curated]
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns["query-results"]]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_query_results_prefix,
        "${var.athena_query_results_prefix}/*",
      ]
    }
  }
  statement {
    sid     = "ReadDocumentObjects"
    actions = ["s3:GetObject"]
    resources = [
      "${var.bucket_arns.curated}/*",
      "${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*",
    ]
  }
  statement {
    sid       = "WriteQueryResults"
    actions   = ["s3:AbortMultipartUpload", "s3:PutObject"]
    resources = ["${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "document_inspector" {
  role   = aws_iam_role.document_inspector.id
  policy = data.aws_iam_policy_document.document_inspector.json
}

resource "aws_iam_role" "lightdash_reader" {
  name               = "${var.name_prefix}-lightdash-reader"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "lightdash_reader" {
  statement {
    sid = "RunAthenaQueries"
    actions = [
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
    ]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    sid = "ReadAnalyticsCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.analytics_database_arns,
      local.analytics_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.lightdash_reader
  }
  statement {
    sid       = "GetAnalyticsBucketLocations"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arns.analytics, var.bucket_arns["query-results"]]
  }
  statement {
    sid       = "ListAnalyticsData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.analytics]
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns["query-results"]]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_query_results_prefix,
        "${var.athena_query_results_prefix}/*",
      ]
    }
  }
  statement {
    sid     = "ReadAnalyticsObjects"
    actions = ["s3:GetObject"]
    resources = [
      "${var.bucket_arns.analytics}/*",
      "${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*",
    ]
  }
  statement {
    sid       = "WriteQueryResults"
    actions   = ["s3:AbortMultipartUpload", "s3:PutObject"]
    resources = ["${var.bucket_arns["query-results"]}/${var.athena_query_results_prefix}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "lightdash_reader" {
  role   = aws_iam_role.lightdash_reader.id
  policy = data.aws_iam_policy_document.lightdash_reader.json
}
