"""Stable logical data assets shared by producer and consumer DAGs."""

from airflow.sdk import Asset

CURATED_ARXIV_METADATA = Asset("lakehouse://curated/arxiv/metadata")
CURATED_GITHUB = Asset("lakehouse://curated/github")
CURATED_MARKET_DATA = Asset("lakehouse://curated/market-data")
ANALYTICS_ENGINEERING = Asset("lakehouse://analytics/engineering")
ANALYTICS_RESEARCH = Asset("lakehouse://analytics/research")
