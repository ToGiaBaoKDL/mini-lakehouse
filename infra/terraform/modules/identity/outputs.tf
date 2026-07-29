output "airflow_role_arn" {
  value = aws_iam_role.airflow.arn
}

output "emr_runtime_role_arn" {
  value = aws_iam_role.emr_runtime.arn
}

output "emr_deployer_role_arn" {
  value = aws_iam_role.emr_deployer.arn
}

output "catalog_admin_role_arn" {
  value = aws_iam_role.catalog_admin.arn
}

output "document_inspector_role_arn" {
  value = aws_iam_role.document_inspector.arn
}

output "dbt_transformer_role_arn" {
  value = aws_iam_role.dbt_transformer.arn
}

output "lightdash_reader_role_arn" {
  value = aws_iam_role.lightdash_reader.arn
}
