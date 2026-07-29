output "arns" {
  value = { for key, parameter in aws_ssm_parameter.this : key => parameter.arn }
}

output "names" {
  value = { for key, parameter in aws_ssm_parameter.this : key => parameter.name }
}
