import base64
import json
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class SecretBox:
    """Symmetric encryption for credentials at rest, keyed by DEM_SECRET_KEY."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    """A fresh Fernet key for DEM_SECRET_KEY."""
    return Fernet.generate_key().decode()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt_bundle(payload: dict[str, object], passphrase: str) -> bytes:
    """Encrypt a config bundle with a user passphrase so it is portable across instances."""
    salt = os.urandom(16)
    fernet = Fernet(_derive_key(passphrase, salt))
    token = fernet.encrypt(json.dumps(payload).encode())
    return base64.urlsafe_b64encode(salt) + b"." + token


def decrypt_bundle(blob: bytes, passphrase: str) -> dict[str, object]:
    salt_b64, _, token = blob.partition(b".")
    salt = base64.urlsafe_b64decode(salt_b64)
    fernet = Fernet(_derive_key(passphrase, salt))
    data: dict[str, object] = json.loads(fernet.decrypt(token))
    return data
