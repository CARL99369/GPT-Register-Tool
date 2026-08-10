import pytest

from sms_tool.totp import generate_totp, normalize_totp_secret


def test_generate_totp_matches_rfc6238_sha1_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    assert generate_totp(secret, timestamp=59, digits=8) == "94287082"
    assert generate_totp(secret, timestamp=1111111109, digits=8) == "07081804"


def test_normalize_totp_secret_accepts_spaces_and_padding():
    assert normalize_totp_secret("gezd gn bv gy3t qojq gezd gnbv gy3t qojq===") == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_generate_totp_rejects_invalid_secret():
    with pytest.raises(ValueError, match="Base32"):
        generate_totp("INVALID0SECRET", timestamp=59)
