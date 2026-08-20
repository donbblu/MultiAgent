from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def calculate_total(price: str, quantity: int, discount: str = "0") -> Decimal:
    """Calculate a validated discounted order total rounded to cents."""
    try:
        unit_price = Decimal(price)
        discount_rate = Decimal(discount)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid input") from exc
    if unit_price < 0:
        raise ValueError("invalid input")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("invalid input")
    if not Decimal("0") <= discount_rate <= Decimal("1"):
        raise ValueError("invalid input")
    total = unit_price * quantity * (Decimal("1") - discount_rate)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
