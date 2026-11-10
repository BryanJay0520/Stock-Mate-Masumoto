"""CSRF protection for cookie-based sessions (double-submit pattern).

Because browsers attach our session cookie automatically, a malicious page
could try to submit requests on a user's behalf. Defence: the server issues
a signed CSRF token bound to the session; the frontend reads it from a
non-HttpOnly cookie and sends it back in the ``X-CSRF-Token`` header. A
third-party page cannot read our cookie (same-origin policy), so it cannot
produce a matching header.

The token contains no secrets — it's the session token's hash, signed with
the server secret so it can't be forged for another session.
"""

import hmac

from itsdangerous import BadSignature, URLSafeSerializer

_SALT = "stockmate-csrf-v1"


def _serializer(secret_key: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret_key, salt=_SALT)


def issue_csrf_token(secret_key: str, session_token_hash: str) -> str:
    return _serializer(secret_key).dumps({"session": session_token_hash})


def verify_csrf_token(secret_key: str, token: str, session_token_hash: str) -> bool:
    try:
        payload = _serializer(secret_key).loads(token)
    except BadSignature:
        return False
    expected = payload.get("session", "")
    return hmac.compare_digest(expected, session_token_hash)
