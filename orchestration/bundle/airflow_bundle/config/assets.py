"""Stable logical data assets shared by producer and consumer DAGs."""

from airflow.sdk import Asset

CURATED_ARXIV_METADATA = Asset("lakehouse://curated/arxiv/metadata")
CURATED_ARXIV_OCR = Asset("lakehouse://curated/arxiv/ocr")
CURATED_GITHUB = Asset("lakehouse://curated/github")
ANALYTICS_ENGINEERING = Asset("lakehouse://analytics/engineering")
ANALYTICS_RESEARCH = Asset("lakehouse://analytics/research")
