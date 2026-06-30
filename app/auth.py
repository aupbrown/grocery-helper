"""Password hashing + session helpers for the guest-first account system.

Accounts are optional: generation works for everyone. Registering (typically right after a plan
is generated) turns on persistence. Sessions are a signed cookie (Starlette SessionMiddleware)
holding only the user id; per-user data is scoped in application code by that id.
"""
import bcrypt

from app import storage

EMAIL_MIN = 3
PASSWORD_MIN = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_credentials(email: str, password: str) -> str | None:
    """Return an error message for obviously-bad credentials, or None if they look OK."""
    if "@" not in email or len(email) < EMAIL_MIN:
        return "Enter a valid email address."
    if len(password) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters."
    return None


def login(request, user_id: int) -> None:
    request.session["user_id"] = user_id


def logout(request) -> None:
    request.session.pop("user_id", None)


def current_user(request) -> dict | None:
    """The logged-in user's row, or None for a guest."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return storage.get_user(user_id)
