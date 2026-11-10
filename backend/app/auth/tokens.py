"""Session token generation and hashing.

The raw token lives only in the user's cookie. The database stores its
SHA-256 hash, so a database leak never exposes usable session tokens.
"""

import hashlib
import secrets


def generate_session_token() -> str:
    """Cryptographically secure random token (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
