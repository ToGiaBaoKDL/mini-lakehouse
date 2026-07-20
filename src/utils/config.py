import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Quản lý cấu hình và biến môi trường tập trung sử dụng Pydantic Settings v2.
    Tự động nạp dữ liệu từ file .env nếu có và giải quyết ký tự ngã (~).
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Cấu hình 3 buckets riêng biệt cho 3 layer: landing, curated, analytics
    s3_landing_bucket: str = Field(default="landing", validation_alias="AWS_S3_LANDING_BUCKET")
    s3_curated_bucket: str = Field(default="curated", validation_alias="AWS_S3_CURATED_BUCKET")
    s3_analytics_bucket: str = Field(default="analytics", validation_alias="AWS_S3_ANALYTICS_BUCKET")
    
    # Cấu hình ClickHouse
    clickhouse_host: str = Field(default="localhost", validation_alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, validation_alias="CLICKHOUSE_PORT")
    clickhouse_db: str = Field(default="github_lakehouse", validation_alias="CLICKHOUSE_DB")
    clickhouse_user: str = Field(default="dev_user", validation_alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="dev_password", validation_alias="CLICKHOUSE_PASSWORD")
    
    # Cấu hình dbt
    dbt_profiles_dir: str = Field(default="./dbt_project", validation_alias="DBT_PROFILES_DIR")
    lakehouse_stage: str = Field(default="local", validation_alias="LAKEHOUSE_STAGE")

    # Cấu hình S3 cho SeaweedFS
    s3_endpoint: str = Field(default="http://localhost:8333", validation_alias="AWS_S3_ENDPOINT")
    s3_access_key: str = Field(default="any_key", validation_alias="AWS_ACCESS_KEY_ID")
    s3_secret_key: str = Field(default="any_secret", validation_alias="AWS_SECRET_ACCESS_KEY")
    s3_region: str = Field(default="us-east-1", validation_alias="AWS_DEFAULT_REGION")
    s3_bucket: str = Field(default="lakehouse", validation_alias="AWS_S3_BUCKET")

# Đối tượng cấu hình dùng chung
settings = Settings()
