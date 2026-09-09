"""Exact decimal conventions shared by deterministic trading calculations."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

RATIO_QUANTUM = Decimal("0.00000001")
RATE_QUANTUM = Decimal("0.000001")
BPS_QUANTUM = Decimal("0.0001")
PRICE_QUANTUM = Decimal("0.00000001")


def ratio(
    numerator: Decimal | int,
    denominator: Decimal | int,
    *,
    quantum: Decimal = RATIO_QUANTUM,
) -> Decimal:
    if denominator == 0:
        raise ValueError("ratio denominator must be non-zero")
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )


def basis_points(numerator: Decimal, denominator: Decimal) -> Decimal:
    return ratio(numerator * Decimal(10_000), denominator, quantum=BPS_QUANTUM)


def rate(numerator: int, denominator: int) -> Decimal:
    """Return one stable count ratio, treating an empty population as zero."""
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("rate counts are inconsistent")
    if denominator == 0:
        return Decimal(0)
    return ratio(numerator, denominator, quantum=RATE_QUANTUM)


def quantiles(values: Sequence[Decimal], fractions: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Return deterministic linearly interpolated quantiles for a non-empty sample."""
    if not values:
        raise ValueError("quantiles require at least one value")
    if any(fraction < 0 or fraction > 1 for fraction in fractions):
        raise ValueError("quantile fractions must be between zero and one")
    ordered = sorted(values)
    results: list[Decimal] = []
    for fraction in fractions:
        position = Decimal(len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        results.append(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)
    return tuple(results)
