output "vpc_id" {
  description = "Identifier of the dedicated EMR worker VPC."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Public subnet identifiers used by EMR Serverless workers."
  value       = toset([for subnet in aws_subnet.public : subnet.id])
}

output "security_group_id" {
  description = "Security group identifier used by EMR Serverless workers."
  value       = aws_security_group.workers.id
}
