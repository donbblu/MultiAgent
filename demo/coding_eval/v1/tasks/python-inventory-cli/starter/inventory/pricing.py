from decimal import Decimal, ROUND_HALF_UP


def calculate_total(price: str, quantity: int, discount: str = "0") -> Decimal:
    """Calculate an order total rounded to cents."""
    del discount
    total = Decimal(price) * quantity
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
