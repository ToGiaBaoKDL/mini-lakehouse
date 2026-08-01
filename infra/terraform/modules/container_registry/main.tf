resource "aws_ecr_repository" "this" {
  for_each = var.repositories

  name                 = "${var.name_prefix}-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, { Service = each.key })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest ${var.retained_image_count} immutable releases"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.retained_image_count
      }
      action = {
        type = "expire"
      }
    }]
  })
}
