"""Strict YAML loading for declarative platform configuration."""

from pathlib import Path
from typing import cast

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode


def _reject_duplicate_keys(node: Node, path: tuple[str, ...] = ()) -> None:
    if isinstance(node, MappingNode):
        keys = set()
        for key_node, value_node in node.value:
            key = key_node.value
            if key in keys:
                raise ValueError(f"Duplicate YAML key: {'.'.join((*path, key))}")
            keys.add(key)
            _reject_duplicate_keys(value_node, (*path, key))
    elif isinstance(node, SequenceNode):
        for index, child in enumerate(node.value):
            _reject_duplicate_keys(child, (*path, str(index)))


def read_yaml(path: Path) -> dict[str, object]:
    try:
        source = path.read_text(encoding="utf-8")
        document = yaml.compose(source, Loader=yaml.SafeLoader)
        if document is None:
            raise ValueError("YAML document is empty")
        _reject_duplicate_keys(document)
        payload = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"YAML {path} must contain a mapping")
    return cast(dict[str, object], payload)
