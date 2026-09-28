"""Point-in-time index membership used to scope breadth capture."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from t0_trading.evidence import public_value
from t0_trading.identity import canonical_json, sha256


class _MarketData(Protocol):
    def get_securities_info_by_index(self, index: str) -> object: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IndexMembership(_StrictModel):
    index: str
    status: Literal["captured", "unavailable"]
    symbols: tuple[str, ...] = ()
    record_sha256s: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_membership(self) -> IndexMembership:
        if not self.index or self.index != self.index.strip().upper():
            raise ValueError("membership index must be an uppercase identifier")
        if tuple(sorted(set(self.symbols))) != self.symbols or any(
            not symbol or symbol != symbol.strip().upper() for symbol in self.symbols
        ):
            raise ValueError("membership symbols must be sorted unique uppercase identifiers")
        captured = self.status == "captured"
        if captured != bool(self.symbols) or len(self.record_sha256s) != len(self.symbols):
            raise ValueError("membership status and records are inconsistent")
        if any(
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
            for digest in self.record_sha256s
        ):
            raise ValueError("membership record checksums must be lowercase SHA-256")
        return self


class BreadthMembershipSnapshot(_StrictModel):
    schema_version: Literal[1] = 1
    captured_at: datetime
    memberships: tuple[IndexMembership, ...]
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("captured_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("membership capture timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_snapshot(self) -> BreadthMembershipSnapshot:
        indices = tuple(membership.index for membership in self.memberships)
        if not indices or len(indices) != len(set(indices)):
            raise ValueError("membership snapshot indices must be non-empty and unique")
        if self.snapshot_sha256 != _snapshot_sha256(self.captured_at, self.memberships):
            raise ValueError("membership snapshot checksum is inconsistent")
        return self

    @property
    def indices(self) -> tuple[str, ...]:
        return tuple(membership.index for membership in self.memberships)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    symbol
                    for membership in self.memberships
                    if membership.status == "captured"
                    for symbol in membership.symbols
                }
            )
        )


def _snapshot_sha256(
    captured_at: datetime,
    memberships: tuple[IndexMembership, ...],
) -> str:
    return sha256(
        canonical_json(
            {
                "schema_version": 1,
                "captured_at": captured_at.astimezone(UTC).isoformat(),
                "memberships": [membership.model_dump(mode="json") for membership in memberships],
            }
        )
    )


def _records(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def capture_index_memberships(
    market: _MarketData,
    indices: tuple[str, ...],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BreadthMembershipSnapshot:
    """Resolve current membership through the official SDK without blocking core capture."""
    memberships: list[IndexMembership] = []
    for index in indices:
        try:
            records = _records(market.get_securities_info_by_index(index))
        except Exception:  # Provider boundary: availability is explicit in captured lineage.
            records = ()
        if not records:
            memberships.append(IndexMembership(index=index, status="unavailable"))
            continue

        by_symbol: dict[str, str] = {}
        for record in records:
            symbol = _field(record, "symbol")
            if not isinstance(symbol, str) or not (normalized := symbol.strip().upper()):
                raise ValueError(f"SSI membership for {index} contains an invalid symbol")
            if normalized in by_symbol:
                raise ValueError(f"SSI membership for {index} contains duplicate symbols")
            by_symbol[normalized] = sha256(canonical_json(public_value(record)))
        ordered = tuple(sorted(by_symbol))
        memberships.append(
            IndexMembership(
                index=index,
                status="captured",
                symbols=ordered,
                record_sha256s=tuple(by_symbol[symbol] for symbol in ordered),
            )
        )

    captured_at = clock().astimezone(UTC)
    values = tuple(memberships)
    return BreadthMembershipSnapshot(
        captured_at=captured_at,
        memberships=values,
        snapshot_sha256=_snapshot_sha256(captured_at, values),
    )
