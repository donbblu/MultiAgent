import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from inventory.pricing import calculate_total


def rejects(*args) -> bool:
    try:
        calculate_total(*args)
    except ValueError:
        return True
    return False


def main() -> int:
    try:
        checks = (
            calculate_total("19.99", 3, "0.10") == Decimal("53.97"),
            rejects("-1.00", 2, "0"),
            rejects("10.00", 0, "0"),
            rejects("10.00", 1, "1.01"),
        )
    except Exception:
        checks = (False,)
    if all(checks):
        print("hidden checks passed")
        return 0
    print("hidden checks failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
