"""Money helpers. All amounts are Decimal BRL with two decimal places."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_brl(value: Decimal) -> str:
    """Brazilian format: Decimal("2199") -> "R$ 2.199,00"."""
    integer, cents = f"{to_money(value):,.2f}".split(".")
    return f"R$ {integer.replace(',', '.')},{cents}"
