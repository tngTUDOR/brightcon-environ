"""Webhook authentication."""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
SIGNATURE_PREFIX = "sha256="
MAX_BODY_BYTES = 5 * 1024 * 1024


def sign(body: bytes, secret: str) -> str:
    """The value GitHub puts in ``X-Hub-Signature-256`` for ``body``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time comparison against the expected HMAC-SHA256 signature."""
    if not signature or not signature.startswith(SIGNATURE_PREFIX):
        return False
    return hmac.compare_digest(sign(body, secret), signature)


def verify_token(presented: str | None, expected: str) -> bool:
    """Constant-time check of a ``Authorization: Bearer <token>`` header."""
    if not presented:
        return False
    scheme, _, value = presented.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip(), expected)
