from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """The minimum config needed before the database (and its stored config) is reachable.

    Everything else — Plane/GitHub/Claude credentials, models, notifications — is stored in
    the DB (see catalog.py + store.py), seeded from these same env vars on first boot.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Root of trust for encrypting stored secrets. Cannot live in the DB it decrypts.
    dem_secret_key: str
    database_url: str = "sqlite+aiosqlite:////data/conductor.db"
    conductor_host: str = "0.0.0.0"
    conductor_port: int = 8420

    # Seed-once by default; DB wins thereafter. Set true to re-import env/yml over the DB
    # (for IaC-driven rotation).
    reseed_from_env: bool = False
    # Optional YAML file to seed config from on first boot, in addition to env vars.
    config_seed_file: Path | None = None

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "BootstrapSettings":
        try:
            Fernet(self.dem_secret_key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "DEM_SECRET_KEY is not a valid Fernet key. Generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`.'
            ) from exc
        return self


_settings: BootstrapSettings | None = None


def get_settings() -> BootstrapSettings:
    global _settings
    if _settings is None:
        _settings = BootstrapSettings()  # type: ignore[call-arg]
    return _settings
