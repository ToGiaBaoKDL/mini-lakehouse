output "application_id" {
  description = "EMR Serverless application identifier."
  value       = aws_emrserverless_application.spark.id
}

output "application_arn" {
  description = "EMR Serverless application ARN."
  value       = aws_emrserverless_application.spark.arn
}
