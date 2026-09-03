from __future__ import annotations

from brightcon_environ.security import sign, verify_signature, verify_token


def test_a_correct_signature_is_accepted():
    body = b'{"ref": "refs/heads/main"}'
    assert verify_signature(body, sign(body, "s3cret"), "s3cret")


def test_signature_matches_the_github_reference_vector():
    # From GitHub's webhook documentation.
    assert sign(b"Hello, World!", "It's a Secret to Everybody") == (
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    )


def test_a_tampered_body_is_rejected():
    signature = sign(b'{"ref": "refs/heads/main"}', "s3cret")
    assert not verify_signature(b'{"ref": "refs/heads/evil"}', signature, "s3cret")


def test_the_wrong_secret_is_rejected():
    body = b"payload"
    assert not verify_signature(body, sign(body, "other"), "s3cret")


def test_a_missing_or_malformed_signature_is_rejected():
    body = b"payload"
    assert not verify_signature(body, None, "s3cret")
    assert not verify_signature(body, "", "s3cret")
    assert not verify_signature(
        body, sign(body, "s3cret").removeprefix("sha256="), "s3cret"
    )
    assert not verify_signature(body, "sha1=deadbeef", "s3cret")


def test_bearer_tokens():
    assert verify_token("Bearer abc123", "abc123")
    assert verify_token("bearer abc123", "abc123")
    assert not verify_token("Bearer wrong", "abc123")
    assert not verify_token("abc123", "abc123")
    assert not verify_token(None, "abc123")
