output "bucket_names" {
  description = "S3 bucket names keyed by logical storage tier."
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.id }
}

output "bucket_arns" {
  description = "S3 bucket ARNs keyed by logical storage tier."
  value       = { for name, bucket in aws_s3_bucket.this : name => bucket.arn }
}

output "kms_key_arn" {
  description = "Customer-managed KMS key used by lakehouse data buckets."
  value       = aws_kms_key.lakehouse.arn
}
