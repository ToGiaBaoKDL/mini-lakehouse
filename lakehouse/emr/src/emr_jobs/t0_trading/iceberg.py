"""Shared immutable Iceberg publication primitives for T0 facts."""

from __future__ import annotations

from collections.abc import Sequence

from pyspark.sql import SparkSession


def _join(keys: Sequence[str]) -> str:
    return " AND ".join(f"target.{key} = source.{key}" for key in keys)


def require_compatible(
    spark: SparkSession,
    *,
    view: str,
    target: str,
    keys: Sequence[str],
    fingerprint: str,
) -> None:
    conflict = spark.sql(
        f"""
        SELECT 1
        FROM {view} source
        JOIN {target} target ON {_join(keys)}
        WHERE target.{fingerprint} != source.{fingerprint}
        LIMIT 1
        """
    ).count()
    if conflict:
        raise RuntimeError(f"Immutable T0 fact conflict in {target}")


def insert_missing(
    spark: SparkSession,
    *,
    view: str,
    target: str,
    keys: Sequence[str],
) -> None:
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING {view} source
        ON {_join(keys)}
        WHEN NOT MATCHED THEN INSERT *
        """
    )
