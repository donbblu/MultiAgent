def validate_user_payload(payload: object) -> dict[str, str]:
    """Return deterministic errors for a create-user payload."""
    if not isinstance(payload, dict):
        return {"payload": "must be an object"}

    errors: dict[str, str] = {}
    unknown = sorted(set(payload) - {"email", "age"})
    if unknown:
        errors["fields"] = f"unknown fields: {', '.join(unknown)}"

    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        errors["email"] = "must be a non-empty string"

    age = payload.get("age")
    if age is not None and (
        isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 120
    ):
        errors["age"] = "must be an integer from 0 to 120"
    return errors
