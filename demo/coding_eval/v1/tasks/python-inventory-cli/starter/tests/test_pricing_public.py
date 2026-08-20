import unittest
from decimal import Decimal

from inventory.pricing import calculate_total


class PricingPublicTests(unittest.TestCase):
    def test_total_without_discount(self) -> None:
        self.assertEqual(calculate_total("10.00", 2), Decimal("20.00"))


if __name__ == "__main__":
    unittest.main()
