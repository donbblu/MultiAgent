import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from user_api import validate_user_payload


def main() -> int:
    try:
        checks = (
            validate_user_payload([]) == {"payload": "must be an object"},
            validate_user_payload({"email": "   "})
            == {"email": "must be a non-empty string"},
            validate_user_payload({"email": "a@b.co", "age": True})
            == {"age": "must be an integer from 0 to 120"},
            validate_user_payload({"email": "a@b.co", "age": 121})
            == {"age": "must be an integer from 0 to 120"},
            validate_user_payload({"email": "a@b.co", "admin": True})
            == {"fields": "unknown fields: admin"},
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
