from decimal import Decimal, ROUND_HALF_UP


def calculate_tax(amount: float, rate: float) -> float:
    """Return tax using decimal half-up rounding to two places."""
    value = Decimal(str(amount)) * Decimal(str(rate))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
