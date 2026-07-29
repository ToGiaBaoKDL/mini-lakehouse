from typing import Any, cast

import pytest
from document_ocr.settings import KaggleSettings, ModalSettings
from pydantic import ValidationError

from lakehouse_platform.config.settings import Settings


def test_runtime_settings_only_contain_process_configuration() -> None:
    settings = Settings(environment="dev")

    assert settings.aws_region == "ap-southeast-1"
    assert settings.aws_profile is None
    assert "landing_uri" not in type(settings).model_fields
    assert "catalog_name" not in type(settings).model_fields


def test_aws_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_PROFILE", "lakehouse-dev-catalog-admin")

    settings = Settings(environment="dev")

    assert settings.aws_region == "us-east-1"
    assert settings.aws_profile == "lakehouse-dev-catalog-admin"


def test_modal_credentials_must_be_configured_as_a_pair() -> None:
    with pytest.raises(ValidationError, match="token_id and token_secret"):
        cast(Any, ModalSettings)(token_id="ak-incomplete", _env_file=None)


def test_kaggle_uses_sdk_standard_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAGGLE_USERNAME", "owner")
    monkeypatch.setenv("KAGGLE_API_TOKEN", "secret")

    settings: KaggleSettings = cast(Any, KaggleSettings)(_env_file=None)

    assert settings.username == "owner"
    assert settings.configured
