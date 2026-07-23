import os

import pytest

from mini_lakehouse.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def require_disposable_lakehouse() -> None:
    """Prevent stateful integration tests from mutating a developer's local catalog."""
    if os.getenv("RUN_LAKEHOUSE_INTEGRATION") != "1":
        return
    if get_settings().environment != "ci":
        pytest.fail(
            "Lakehouse integration tests mutate catalog state and require "
            "LAKEHOUSE_ENVIRONMENT=ci on a disposable stack"
        )
