output "repository_arns" {
  description = "ECR repository ARNs keyed by deployable service."
  value       = { for name, repository in aws_ecr_repository.this : name => repository.arn }
}

output "repository_urls" {
  description = "ECR repository URLs keyed by deployable service."
  value       = { for name, repository in aws_ecr_repository.this : name => repository.repository_url }
}
