import unittest

from tax import calculate_tax


class CalculateTaxPublicTests(unittest.TestCase):
    def test_regular_amount(self) -> None:
        self.assertEqual(calculate_tax(100, 0.13), 13.0)

    def test_result_has_two_decimal_precision(self) -> None:
        self.assertEqual(calculate_tax(19.99, 0.05), 1.0)


if __name__ == "__main__":
    unittest.main()
