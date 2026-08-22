import re
from datetime import UTC, datetime, timedelta

from core.tokens import (
    TOKEN_BYTES,
    IssuedApprovalToken,
    generate_raw_token,
    hash_token,
    is_expired,
    mint_approval_token,
    tokens_match,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# base64url alphabet, no padding.
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# --- generate_raw_token -------------------------------------------------------


def test_generate_raw_token_is_base64url_with_no_padding() -> None:
    token = generate_raw_token()
    assert "=" not in token
    assert _BASE64URL_RE.match(token)


def test_generate_raw_token_decodes_back_to_32_bytes() -> None:
    import base64

    token = generate_raw_token()
    padded = token + "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode(padded)
    assert len(decoded) == TOKEN_BYTES


def test_generate_raw_token_is_random_each_call() -> None:
    tokens = {generate_raw_token() for _ in range(100)}
    assert len(tokens) == 100


# --- hash_token ----------------------------------------------------------


def test_hash_token_is_a_64_char_hex_sha256_digest() -> None:
    digest = hash_token("some-raw-token")
    assert len(digest) == 64
    assert re.match(r"^[0-9a-f]{64}$", digest)


def test_hash_token_is_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")


def test_hash_token_differs_for_different_input() -> None:
    assert hash_token("abc") != hash_token("abd")


def test_hash_token_never_returns_the_raw_token() -> None:
    raw = "a-very-recognizable-raw-token-value"
    assert raw not in hash_token(raw)


# --- tokens_match --------------------------------------------------------


def test_tokens_match_true_for_correct_token() -> None:
    raw = generate_raw_token()
    assert tokens_match(raw, hash_token(raw)) is True


def test_tokens_match_false_for_wrong_token() -> None:
    raw = generate_raw_token()
    other = generate_raw_token()
    assert tokens_match(other, hash_token(raw)) is False


def test_tokens_match_false_for_tampered_token() -> None:
    raw = generate_raw_token()
    stored_hash = hash_token(raw)
    tampered = raw[:-1] + ("a" if raw[-1] != "a" else "b")
    assert tokens_match(tampered, stored_hash) is False


# --- is_expired ------------------------------------------------------------


def test_is_expired_false_before_expiry() -> None:
    assert is_expired(NOW + timedelta(hours=1), now=NOW) is False


def test_is_expired_true_after_expiry() -> None:
    assert is_expired(NOW - timedelta(hours=1), now=NOW) is True


def test_is_expired_true_exactly_at_expiry() -> None:
    # Inclusive boundary: a token is not valid for one more instant at
    # exactly its expiry time.
    assert is_expired(NOW, now=NOW) is True


# --- mint_approval_token ---------------------------------------------------


def test_mint_approval_token_sets_expiry_from_ttl_and_now() -> None:
    issued = mint_approval_token(timedelta(hours=72), now=NOW)
    assert issued.expires_at == NOW + timedelta(hours=72)


def test_mint_approval_token_hash_matches_raw_token() -> None:
    issued = mint_approval_token(timedelta(hours=1), now=NOW)
    assert tokens_match(issued.raw_token, issued.token_hash)


def test_mint_approval_token_is_random_each_call() -> None:
    a = mint_approval_token(timedelta(hours=1), now=NOW)
    b = mint_approval_token(timedelta(hours=1), now=NOW)
    assert a.raw_token != b.raw_token
    assert a.token_hash != b.token_hash


# --- IssuedApprovalToken never leaks the raw token via repr -----------------


def test_issued_approval_token_repr_redacts_raw_token() -> None:
    issued = mint_approval_token(timedelta(hours=1), now=NOW)
    rendered = repr(issued)
    assert issued.raw_token not in rendered
    assert "<redacted>" in rendered
    assert issued.token_hash in rendered  # the hash itself is fine to show


def test_issued_approval_token_str_also_redacts_raw_token() -> None:
    # dataclasses without a custom __str__ fall back to __repr__.
    issued = mint_approval_token(timedelta(hours=1), now=NOW)
    assert issued.raw_token not in str(issued)


def test_issued_approval_token_constructed_directly_still_redacts() -> None:
    token = IssuedApprovalToken(
        raw_token="a-recognizable-raw-value", token_hash=hash_token("a-recognizable-raw-value"),
        expires_at=NOW,
    )
    assert "a-recognizable-raw-value" not in repr(token)
    assert "<redacted>" in repr(token)
