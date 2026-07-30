import re
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from lakehouse_platform.config.yaml import read_yaml
from lakehouse_platform.contracts.curated import CuratedProductContract
from lakehouse_platform.contracts.domains import DomainContract
from lakehouse_platform.contracts.registry import DataContracts
from lakehouse_platform.contracts.sources import SourceContract

ContractT = TypeVar("ContractT", bound=BaseModel)

_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[-_])(password|secret|token|credential|access[-_]?key|client[-_]?secret)(?:$|[-_])",
    re.IGNORECASE,
)


def _reject_secret_keys(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            if _SECRET_KEY_PATTERN.search(key):
                raise ValueError(
                    f"Secret-like key is not allowed in declarative contracts: "
                    f"{'.'.join((*path, key))}"
                )
            _reject_secret_keys(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, (*path, str(index)))


def _read_contract(path: Path) -> dict[str, object]:
    payload = read_yaml(path)
    _reject_secret_keys(payload)
    return payload


def _load_collection(  # noqa: UP047
    root: Path,
    folder: str,
    adapter: TypeAdapter[ContractT],
) -> tuple[ContractT, ...]:
    directory = root / folder
    paths = sorted(directory.rglob("*.yaml"))
    contracts: list[ContractT] = []
    for path in paths:
        try:
            contracts.append(adapter.validate_python(_read_contract(path)))
        except ValueError as error:
            raise ValueError(f"Invalid contract {path}: {error}") from error
    return tuple(contracts)


@lru_cache(maxsize=8)
def load_contracts(root: Path = Path("contracts")) -> DataContracts:
    resolved = root.resolve()
    return DataContracts(
        sources=_load_collection(resolved, "sources", TypeAdapter(SourceContract)),
        curated=_load_collection(
            resolved,
            "curated",
            TypeAdapter(CuratedProductContract),
        ),
        domains=_load_collection(resolved, "domains", TypeAdapter(DomainContract)),
    )
