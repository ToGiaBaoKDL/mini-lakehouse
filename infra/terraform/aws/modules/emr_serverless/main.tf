resource "aws_emrserverless_application" "spark" {
  name          = var.name
  release_label = var.release_label
  type          = "spark"

  auto_start_configuration {
    enabled = true
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = var.idle_timeout_minutes
  }

  maximum_capacity {
    cpu    = var.maximum_capacity.cpu
    memory = var.maximum_capacity.memory
    disk   = var.maximum_capacity.disk
  }

  scheduler_configuration {
    max_concurrent_runs   = var.scheduler.max_concurrent_runs
    queue_timeout_minutes = var.scheduler.queue_timeout_minutes
  }

  network_configuration {
    security_group_ids = var.security_group_ids
    subnet_ids         = var.subnet_ids
  }

  monitoring_configuration {
    managed_persistence_monitoring_configuration {
      enabled = true
    }
  }

  runtime_configuration {
    classification = "spark-defaults"
    properties = merge({
      "spark.jars"                                          = "/usr/share/aws/iceberg/lib/iceberg-spark3-runtime.jar"
      "spark.sql.extensions"                                = "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
      "spark.sql.catalog.${var.catalog_alias}"              = "org.apache.iceberg.spark.SparkCatalog"
      "spark.sql.catalog.${var.catalog_alias}.catalog-impl" = "org.apache.iceberg.aws.glue.GlueCatalog"
      "spark.sql.catalog.${var.catalog_alias}.io-impl"      = "org.apache.iceberg.aws.s3.S3FileIO"
      "spark.dynamicAllocation.enabled"                     = "true"
      "spark.sql.session.timeZone"                          = "UTC"
    }, var.spark_properties)
  }

  runtime_configuration {
    classification = "spark"
    properties = {
      dynamicAllocationOptimization = "true"
    }
  }

  tags = var.tags
}
