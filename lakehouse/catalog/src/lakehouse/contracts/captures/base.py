"""Primitives shared by immutable capture manifests."""

from typing import Annotated

from pydantic import StringConstraints

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
