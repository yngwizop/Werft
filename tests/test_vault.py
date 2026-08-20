import json
from pathlib import Path

from app.core.auth import (
    INIT_PASSWORD,
    allow_login_attempt,
    decode_session,
    encode_session,
    hash_password,
    password_acceptable,
    verify_password,
)
from app.core.config import get_infra
from app.core.crypto import decrypt_blob, encrypt_blob, reset_master_key_cache
from app.core.runtime_settings import mask_settings, merge_overlay
from app.core.security import compute_signature, hmac_secret_usable, ip_allowed


def test_password_hash_and_policy() -> None:
    hashed = hash_password("a-long-enough-secret")
    assert hashed != "a-long-enough-secret"
    assert verify_password("a-long-enough-secret", hashed)
    assert not verify_password("wrong", hashed)
    assert password_acceptable(INIT_PASSWORD)
    assert password_acceptable("short")
    assert password_acceptable("a-long-enough-secret") is None


def test_session_roundtrip() -> None:
    token = encode_session("admin", 3)
    payload = decode_session(token)
    assert payload is not None
    assert payload["sub"] == "admin"
    assert payload["sv"] == 3
    assert decode_session("not-a-token") is None


def test_login_rate_limit() -> None:
    from app.core import auth as auth_mod

    auth_mod._login_hits.clear()
    ip = "203.0.113.9"
    for _ in range(5):
        assert allow_login_attempt(ip)
    assert not allow_login_attempt(ip)


def test_encrypt_decrypt_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WERFT_MASTER_KEY_PATH", str(tmp_path / "master.key"))
    get_infra.cache_clear()
    reset_master_key_cache()
    from app.core.crypto import load_or_create_master_key

    key = load_or_create_master_key()
    nonce, blob = encrypt_blob(b'{"netbox_token":"abc"}', key)
    assert decrypt_blob(nonce, blob, key) == b'{"netbox_token":"abc"}'
    assert (tmp_path / "master.key").stat().st_mode & 0o777 == 0o600
    get_infra.cache_clear()
    reset_master_key_cache()


def test_settings_masking() -> None:
    settings = merge_overlay({"netbox_token": "super-secret", "netbox_url": "http://nb"})
    masked = mask_settings(settings)
    assert masked["netbox_url"] == "http://nb"
    assert masked["netbox_token"] == {"configured": True, "value": ""}
    dumped = json.dumps(masked)
    assert "super-secret" not in dumped


def test_ip_allowlist() -> None:
    assert ip_allowed("192.0.2.20", "")
    assert ip_allowed("192.0.2.20", "192.0.2.20")
    assert not ip_allowed("10.0.0.1", "192.0.2.20")
    assert ip_allowed("192.0.2.50", "192.0.2.0/24")


def test_hmac_stable() -> None:
    assert compute_signature("secret", b"abc") == compute_signature("secret", b"abc")


def test_hmac_secret_policy() -> None:
    assert not hmac_secret_usable("change-me")
    assert not hmac_secret_usable("short")
    assert hmac_secret_usable("a-long-enough-hmac-secret")
