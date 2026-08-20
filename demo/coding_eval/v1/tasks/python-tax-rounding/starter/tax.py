def calculate_tax(amount: float, rate: float) -> float:
    """Return tax rounded to two decimal places."""
    return round(amount * rate, 2)
