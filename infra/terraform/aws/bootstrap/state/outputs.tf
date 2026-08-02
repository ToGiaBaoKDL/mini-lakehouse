output "bucket_name" {
  description = "Versioned S3 bucket used by Terraform remote state."
  value       = aws_s3_bucket.state.id
}
