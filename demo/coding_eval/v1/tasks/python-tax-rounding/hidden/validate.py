import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tax import calculate_tax


def main() -> int:
    checks = (
        calculate_tax(10.05, 0.10) == 1.01,
        calculate_tax(2.675, 1.0) == 2.68,
        calculate_tax(0.045, 1.0) == 0.05,
    )
    if all(checks):
        print("hidden checks passed")
        return 0
    print("hidden checks failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
