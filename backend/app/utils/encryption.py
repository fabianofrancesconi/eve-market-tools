"""Fernet-based token encryption for EVE refresh tokens at rest."""
from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib

from ..config import settings


def _get_fernet() -> Fernet:
    key = settings.token_encryption_key.encode()
    # Derive a valid 32-byte Fernet key from the config string
    derived = hashlib.sha256(key).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a refresh token for database storage."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a refresh token from database storage. Raises ValueError on failure."""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt token — key may have changed")
