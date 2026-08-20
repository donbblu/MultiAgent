def validate_user_payload(payload: dict) -> dict[str, str]:
    """Return validation errors for a create-user payload."""
    errors: dict[str, str] = {}
    if payload.get("email") is None:
        errors["email"] = "must be a non-empty string"
    age = payload.get("age")
    if age is not None and age < 0:
        errors["age"] = "must be an integer from 0 to 120"
    return errors
