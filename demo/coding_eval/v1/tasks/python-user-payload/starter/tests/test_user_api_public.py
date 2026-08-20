import unittest

from user_api import validate_user_payload


class UserPayloadPublicTests(unittest.TestCase):
    def test_valid_payload_has_no_errors(self) -> None:
        self.assertEqual(
            validate_user_payload({"email": "dev@example.com", "age": 28}),
            {},
        )

    def test_email_is_required(self) -> None:
        self.assertEqual(
            validate_user_payload({"age": 28}),
            {"email": "must be a non-empty string"},
        )


if __name__ == "__main__":
    unittest.main()
