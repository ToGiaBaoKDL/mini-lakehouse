data "aws_iam_policy_document" "github_main_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [var.github_main_subject]
    }
  }
}

data "aws_iam_policy_document" "github_image_publisher_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [var.github_main_subject, var.github_environment_subject]
    }
  }
}

data "aws_iam_policy_document" "github_environment_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [var.github_environment_subject]
    }
  }
}

resource "aws_iam_role" "github_image_publisher" {
  name               = "${var.name_prefix}-github-image-publisher"
  assume_role_policy = data.aws_iam_policy_document.github_image_publisher_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "github_image_publisher" {
  role   = aws_iam_role.github_image_publisher.id
  policy = data.aws_iam_policy_document.image_publisher.json
}

resource "aws_iam_role" "github_emr_publisher" {
  name               = "${var.name_prefix}-github-emr-publisher"
  assume_role_policy = data.aws_iam_policy_document.github_main_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "github_emr_publisher" {
  role   = aws_iam_role.github_emr_publisher.id
  policy = data.aws_iam_policy_document.emr_publisher.json
}

resource "aws_iam_role" "github_docs_deployer" {
  name               = "${var.name_prefix}-github-docs-deployer"
  assume_role_policy = data.aws_iam_policy_document.github_environment_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "github_docs_deployer" {
  statement {
    sid       = "ReadCloudflareDocsCiToken"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.cloudflare_docs_ci_secret_arn]
  }
}

resource "aws_iam_role_policy" "github_docs_deployer" {
  role   = aws_iam_role.github_docs_deployer.id
  policy = data.aws_iam_policy_document.github_docs_deployer.json
}

resource "aws_iam_role" "github_lightdash_deployer" {
  name               = "${var.name_prefix}-github-lightdash-deployer"
  assume_role_policy = data.aws_iam_policy_document.github_environment_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "github_lightdash_deployer" {
  statement {
    sid       = "ReadLightdashCiToken"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.lightdash_ci_secret_arn]
  }
}

resource "aws_iam_role_policy" "github_lightdash_deployer" {
  role   = aws_iam_role.github_lightdash_deployer.id
  policy = data.aws_iam_policy_document.github_lightdash_deployer.json
}

resource "aws_iam_role" "github_signoz_deployer" {
  name               = "${var.name_prefix}-github-signoz-deployer"
  assume_role_policy = data.aws_iam_policy_document.github_environment_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "github_signoz_deployer" {
  statement {
    sid       = "ReadSignozCiToken"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.signoz_ci_secret_arn]
  }

  statement {
    sid = "ManageSignozTerraformState"
    actions = [
      "s3:ListBucket",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::*-terraform-state-${var.account_id}",
      "arn:${data.aws_partition.current.partition}:s3:::*-terraform-state-${var.account_id}/lakehouse/signoz/*",
    ]
  }
}

resource "aws_iam_role_policy" "github_signoz_deployer" {
  role   = aws_iam_role.github_signoz_deployer.id
  policy = data.aws_iam_policy_document.github_signoz_deployer.json
}
