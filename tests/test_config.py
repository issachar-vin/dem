import pytest
from pydantic import ValidationError

from conductor.config import BootstrapSettings
from conductor.crypto import generate_key


def test_valid_fernet_key_accepted() -> None:
    key = generate_key()
    settings = BootstrapSettings(_env_file=None, dem_secret_key=key)  # type: ignore[call-arg]
    assert settings.dem_secret_key == key
    assert settings.reseed_from_env is False


def test_invalid_fernet_key_rejected() -> None:
    with pytest.raises(ValidationError, match="not a valid Fernet key"):
        BootstrapSettings(_env_file=None, dem_secret_key="not-a-key")  # type: ignore[call-arg]


def test_missing_key_rejected() -> None:
    with pytest.raises(ValidationError):
        BootstrapSettings(_env_file=None)  # type: ignore[call-arg]
