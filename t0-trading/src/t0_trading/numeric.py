"""Exact decimal conventions shared by deterministic trading calculations."""

from decimal import ROUND_HALF_UP, Decimal

RATIO_QUANTUM = Decimal("0.00000001")
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
