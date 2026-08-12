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
