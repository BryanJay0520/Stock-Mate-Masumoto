"""Argon2 password hashing (approved stack; decision D7).

Only hashes are ever stored. ``verify_password`` never raises — a malformed
hash or a mismatch both return False, so callers can't accidentally leak why
a check failed.
"""

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


def validate_password_strength(password: str) -> list[str]:
    """Return a list of problems; an empty list means acceptable."""
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isalpha() for c in password):
        errors.append("at least one letter")
    if not any(c.isdigit() for c in password):
        errors.append("at least one digit")
    return errors
