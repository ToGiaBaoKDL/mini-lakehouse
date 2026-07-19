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

    # Cấu hình Iceberg Warehouse Path
    warehouse_path: str = Field(
        default="~/projects/mini-lakehouse/warehouse",
        validation_alias="PYICEBERG_CATALOG__PROD__WAREHOUSE"
    )
    
    @field_validator("warehouse_path", mode="after")
    @classmethod
    def expand_tilde_path(cls, v: str) -> str:
        """Tự động mở rộng ký tự ~ thành đường dẫn tuyệt đối đầy đủ."""
        return os.path.abspath(os.path.expanduser(v))
    
    # Cấu hình ClickHouse
    clickhouse_host: str = Field(default="localhost", validation_alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, validation_alias="CLICKHOUSE_PORT")
    clickhouse_db: str = Field(default="github_lakehouse", validation_alias="CLICKHOUSE_DB")
    clickhouse_user: str = Field(default="dev_user", validation_alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="dev_password", validation_alias="CLICKHOUSE_PASSWORD")
    
    # Cấu hình dbt
    dbt_profiles_dir: str = Field(default="./dbt_project", validation_alias="DBT_PROFILES_DIR")
    lakehouse_stage: str = Field(default="local", validation_alias="LAKEHOUSE_STAGE")

# Đối tượng cấu hình dùng chung
settings = Settings()
