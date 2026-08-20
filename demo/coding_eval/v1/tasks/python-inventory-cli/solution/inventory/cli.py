import argparse

from .pricing import calculate_total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--price", required=True)
    parser.add_argument("--quantity", required=True, type=int)
    parser.add_argument("--discount", default="0")
    args = parser.parse_args()
    try:
        total = calculate_total(args.price, args.quantity, args.discount)
    except ValueError:
        parser.error("invalid input")
    print(f"total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
