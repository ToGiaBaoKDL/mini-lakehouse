import re
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, TypeAdapter
from yaml.nodes import MappingNode, Node, SequenceNode

from mini_lakehouse.contracts.catalog import CatalogContract
from mini_lakehouse.contracts.curated_products import CuratedProductContract
from mini_lakehouse.contracts.domains import DomainContract
from mini_lakehouse.contracts.policies import PolicyContract
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.sources import SourceContract

_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[-_])(password|secret|token|credential|access[-_]?key|client[-_]?secret)(?:$|[-_])",
    re.IGNORECASE,
)


def _reject_duplicate_keys(node: Node, path: tuple[str, ...] = ()) -> None:
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key_node, value_node in node.value:
            key = key_node.value
            if key in keys:
                location = ".".join((*path, key))
                raise ValueError(f"Duplicate YAML key: {location}")
            keys.add(key)
            _reject_duplicate_keys(value_node, (*path, key))
    elif isinstance(node, SequenceNode):
        for index, child in enumerate(node.value):
            _reject_duplicate_keys(child, (*path, str(index)))


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


def _read_yaml(path: Path) -> dict[str, object]:
    try:
        source = path.read_text(encoding="utf-8")
        document = yaml.compose(source, Loader=yaml.SafeLoader)
        if document is None:
            raise ValueError("YAML document is empty")
        _reject_duplicate_keys(document)
        payload = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML contract {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"Contract {path} must contain a YAML mapping")
    _reject_secret_keys(payload)
    return cast(dict[str, object], payload)


def _load_model[ContractT: BaseModel](path: Path, model: type[ContractT]) -> ContractT:
    try:
        return model.model_validate(_read_yaml(path))
    except ValueError as error:
        raise ValueError(f"Invalid contract {path}: {error}") from error


def _load_collection[ContractT: BaseModel](
    root: Path,
    folder: str,
    adapter: TypeAdapter[ContractT],
) -> tuple[ContractT, ...]:
    directory = root / folder
    paths = sorted(directory.rglob("*.yaml"))
    if not paths:
        raise ValueError(f"Contract directory contains no YAML files: {directory}")
    contracts: list[ContractT] = []
    for path in paths:
        try:
            contracts.append(adapter.validate_python(_read_yaml(path)))
        except ValueError as error:
            raise ValueError(f"Invalid contract {path}: {error}") from error
    return tuple(contracts)


@lru_cache(maxsize=8)
def load_contracts(root: Path = Path("contracts")) -> PlatformContracts:
    resolved = root.resolve()
    catalog_path = resolved / "catalog.yaml"
    if not catalog_path.is_file():
        raise ValueError(f"Catalog contract does not exist: {catalog_path}")
    return PlatformContracts(
        catalog=_load_model(catalog_path, CatalogContract),
        sources=_load_collection(resolved, "sources", TypeAdapter(SourceContract)),
        curated_products=_load_collection(
            resolved,
            "curated_products",
            TypeAdapter(CuratedProductContract),
        ),
        domains=_load_collection(resolved, "domains", TypeAdapter(DomainContract)),
        policies=_load_collection(resolved, "policies", TypeAdapter(PolicyContract)),
    )
