"""Tests for Fernet token encryption/decryption."""
import os
import pytest


@pytest.fixture(autouse=True)
def set_encryption_env(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-encryption-key-32bytes!!!!!")


def test_encrypt_decrypt_roundtrip():
    from app.utils.encryption import encrypt_token, decrypt_token
    plaintext = "some-refresh-token-value-abc123"
    ciphertext = encrypt_token(plaintext)
    assert ciphertext != plaintext
    assert decrypt_token(ciphertext) == plaintext


def test_different_plaintexts_produce_different_ciphertexts():
    from app.utils.encryption import encrypt_token
    a = encrypt_token("token_a")
    b = encrypt_token("token_b")
    assert a != b


def test_same_plaintext_produces_different_ciphertexts():
    from app.utils.encryption import encrypt_token
    a = encrypt_token("same-token")
    b = encrypt_token("same-token")
    assert a != b  # Fernet uses random IV


def test_decrypt_with_wrong_key_raises(monkeypatch):
    from app.utils.encryption import encrypt_token
    ciphertext = encrypt_token("secret-token")

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "different-key-entirely-32bytes!!")
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.utils.encryption
    importlib.reload(app.utils.encryption)
    from app.utils.encryption import decrypt_token as decrypt_new

    with pytest.raises(ValueError, match="Failed to decrypt"):
        decrypt_new(ciphertext)
