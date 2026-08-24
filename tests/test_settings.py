import pytest
from lakehouse.config.settings import Settings


def test_runtime_settings_only_contain_process_configuration() -> None:
    settings = Settings(environment="dev")

    assert settings.aws_region == "ap-southeast-1"
    assert "landing_uri" not in type(settings).model_fields
    assert "catalog_name" not in type(settings).model_fields
    assert "aws_profile" not in type(settings).model_fields


def test_aws_region_environment_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    settings = Settings(environment="dev")

    assert settings.aws_region == "us-east-1"
