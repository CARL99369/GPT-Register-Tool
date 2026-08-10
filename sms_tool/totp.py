"""Local RFC 6238 TOTP generation for account MFA."""

import base64
import binascii
import hashlib
import hmac
import re
import struct
import time


_SECRET_RE = re.compile(r"^[A-Z2-7]+$")


def normalize_totp_secret(value: str) -> str:
    """Normalize a Base32 TOTP secret without retaining presentation padding."""
    normalized = re.sub(r"[\s-]+", "", str(value or "")).strip().upper().rstrip("=")
    if not normalized or not _SECRET_RE.fullmatch(normalized):
        raise ValueError("TOTP secret must be a valid Base32 value")
    try:
        base64.b32decode(normalized + ("=" * (-len(normalized) % 8)), casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("TOTP secret must be a valid Base32 value") from exc
    return normalized


def generate_totp(secret: str, timestamp: float | None = None, digits: int = 6, period: int = 30) -> str:
    """Generate a time-based one-time password using HMAC-SHA1."""
    if int(digits) not in (6, 8):
        raise ValueError("TOTP digits must be 6 or 8")
    if int(period) <= 0:
        raise ValueError("TOTP period must be positive")
    normalized = normalize_totp_secret(secret)
    key = base64.b32decode(normalized + ("=" * (-len(normalized) % 8)), casefold=True)
    counter = int((time.time() if timestamp is None else timestamp) // int(period))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** int(digits))).zfill(int(digits))
