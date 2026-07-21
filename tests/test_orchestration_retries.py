import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

from mini_lakehouse.sources.github_archive.client import ArchiveNotPublishedError


def _load_retries() -> ModuleType:
    path = Path("orchestration/utils/retries.py")
    spec = importlib.util.spec_from_file_location("orchestration_retries", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prefect_retries_archive_404_as_a_bounded_publication_delay() -> None:
    state = Mock()
    state.result.side_effect = ArchiveNotPublishedError("not published")

    assert _load_retries().retry_transient_ingestion_error(None, None, state) is True
