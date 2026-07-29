output "bucket_names" {
  value = { for name, bucket in aws_s3_bucket.this : name => bucket.id }
}

output "bucket_arns" {
  value = { for name, bucket in aws_s3_bucket.this : name => bucket.arn }
}

output "kms_key_arn" {
  value = aws_kms_key.lakehouse.arn
}
