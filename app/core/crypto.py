"""AES-256-GCM helpers and the on-disk master key."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_infra

NONCE_SIZE = 12
KEY_SIZE = 32

_cached_key: bytes | None = None


def master_key_path() -> Path:
    return Path(get_infra().werft_master_key_path)


def load_or_create_master_key() -> bytes:
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    path = master_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        key = path.read_bytes()
        if len(key) != KEY_SIZE:
            raise RuntimeError(f"Master key at {path} is not {KEY_SIZE} bytes")
        _cached_key = key
        return key
    key = os.urandom(KEY_SIZE)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        key = path.read_bytes()
        if len(key) != KEY_SIZE:
            raise RuntimeError(f"Master key at {path} is not {KEY_SIZE} bytes")
        _cached_key = key
        return key
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    _cached_key = key
    return key


def reset_master_key_cache() -> None:
    global _cached_key
    _cached_key = None


def encrypt_blob(plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce, ciphertext


def decrypt_blob(nonce: bytes, ciphertext: bytes, key: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, None)
