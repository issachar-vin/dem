import pytest
from cryptography.fernet import InvalidToken

from conductor.crypto import SecretBox, decrypt_bundle, encrypt_bundle, generate_key


def test_secretbox_round_trip() -> None:
    box = SecretBox(generate_key())
    token = box.encrypt("hunter2")
    assert token != "hunter2"
    assert box.decrypt(token) == "hunter2"


def test_secretbox_rejects_foreign_key() -> None:
    token = SecretBox(generate_key()).encrypt("secret")
    with pytest.raises(InvalidToken):
        SecretBox(generate_key()).decrypt(token)


def test_bundle_round_trip() -> None:
    payload = {"secrets": {"plane_api_key": "abc"}, "settings": {"plane_base_url": "https://x"}}
    blob = encrypt_bundle(payload, "correct horse")
    assert decrypt_bundle(blob, "correct horse") == payload


def test_bundle_wrong_passphrase_fails() -> None:
    blob = encrypt_bundle({"secrets": {}, "settings": {}}, "right")
    with pytest.raises(InvalidToken):
        decrypt_bundle(blob, "wrong")


def test_bundle_salt_is_random() -> None:
    payload = {"secrets": {}, "settings": {}}
    assert encrypt_bundle(payload, "pw") != encrypt_bundle(payload, "pw")
