"""Spark lifecycle and catalog guards shared by jobs."""

import os
import sys

from loguru import logger
from pyspark.sql import SparkSession


def configure_logging(job: str, source_date: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=os.getenv("LOGURU_LEVEL", "INFO"),
        serialize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.configure(extra={"job": job, "source_date": source_date})


def session(name: str) -> SparkSession:
    return SparkSession.builder.appName(name).getOrCreate()


def require_tables(spark: SparkSession, tables: tuple[str, ...]) -> None:
    missing = [table for table in tables if not spark.catalog.tableExists(table)]
    if missing:
        raise RuntimeError(
            "Missing contract-managed Iceberg tables; run catalog apply first: "
            + ", ".join(missing)
        )
