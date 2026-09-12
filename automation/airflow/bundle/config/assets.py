"""Stable logical data assets published by producer DAGs."""

from airflow.sdk import Asset

CURATED_ARXIV_METADATA = Asset("lakehouse://curated/arxiv/metadata")
CURATED_GITHUB = Asset("lakehouse://curated/github")
CURATED_MARKET_DATA = Asset("lakehouse://curated/market-data")
CURATED_T0_TRADING = Asset("lakehouse://curated/t0-trading")
