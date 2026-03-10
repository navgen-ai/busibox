"""
Simple value encryption for config entries.

Uses AES-256-GCM with a key derived from CONFIG_ENCRYPTION_KEY.
If no key is set, values are stored/returned as plaintext (dev mode).
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12


def _derive_key(passphrase: str) -> bytes:
    """Derive a 256-bit key from a passphrase using SHA-256."""
    return hashlib.sha256(passphrase.encode()).digest()


def encrypt_value(plaintext: str, passphrase: str) -> str:
    """Encrypt a value. Returns base64-encoded nonce+ciphertext."""
    if not passphrase:
        return plaintext
    key = _derive_key(passphrase)
    nonce = os.urandom(NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_value(stored: str, passphrase: str) -> str:
    """Decrypt a value previously encrypted with encrypt_value."""
    if not passphrase:
        return stored
    raw = base64.b64decode(stored)
    nonce, ct = raw[:NONCE_SIZE], raw[NONCE_SIZE:]
    key = _derive_key(passphrase)
    return AESGCM(key).decrypt(nonce, ct, None).decode()
