from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import catalog
from conductor.crypto import SecretBox, decrypt_bundle, encrypt_bundle
from conductor.models import Secret, Setting


def _last_four(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


class ConfigStore:
    """DB-backed application config. Secrets are encrypted; settings are plain. The DB is the
    source of truth; env/yml only seed it (once, unless reseed is requested)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    # ── secrets ──────────────────────────────────────────────────────────────
    async def set_secret(self, name: str, value: str, *, source: str = "ui") -> None:
        async with self._sessionmaker() as session:
            await self._upsert_secret(session, name, value, source)
            await session.commit()

    async def get_secret(self, name: str) -> str | None:
        async with self._sessionmaker() as session:
            secret = await session.get(Secret, name)
            return self._box.decrypt(secret.ciphertext) if secret else None

    async def _upsert_secret(
        self, session: AsyncSession, name: str, value: str, source: str
    ) -> None:
        secret = await session.get(Secret, name)
        if secret is None:
            session.add(
                Secret(
                    name=name,
                    ciphertext=self._box.encrypt(value),
                    last_four=_last_four(value),
                    source=source,
                )
            )
        else:
            secret.ciphertext = self._box.encrypt(value)
            secret.last_four = _last_four(value)
            secret.source = source

    # ── settings ─────────────────────────────────────────────────────────────
    async def set_setting(self, name: str, value: str, *, source: str = "ui") -> None:
        async with self._sessionmaker() as session:
            await self._upsert_setting(session, name, value, source)
            await session.commit()

    async def get_setting(self, name: str) -> str | None:
        async with self._sessionmaker() as session:
            setting = await session.get(Setting, name)
            return setting.value if setting else None

    async def _upsert_setting(
        self, session: AsyncSession, name: str, value: str, source: str
    ) -> None:
        setting = await session.get(Setting, name)
        if setting is None:
            session.add(Setting(name=name, value=value, source=source))
        else:
            setting.value = value
            setting.source = source

    # ── resolved view + seeding ──────────────────────────────────────────────
    async def resolved(self) -> dict[str, str]:
        """Catalog defaults overlaid with stored settings and decrypted secrets. Internal use
        only (holds plaintext secrets) — never return this from the API."""
        values = catalog.defaults()
        async with self._sessionmaker() as session:
            for setting in (await session.execute(select(Setting))).scalars():
                values[setting.name] = setting.value
            for secret in (await session.execute(select(Secret))).scalars():
                values[secret.name] = self._box.decrypt(secret.ciphertext)
        return values

    async def seed_from_env(self, env: Mapping[str, str], *, reseed: bool) -> int:
        """Seed each catalog field from env vars. Skips keys already in the DB unless reseed."""
        seeded = 0
        async with self._sessionmaker() as session:
            for field in catalog.CATALOG:
                raw = env.get(field.env)
                if not raw:
                    continue
                model: type[Secret] | type[Setting] = Secret if field.secret else Setting
                if await session.get(model, field.name) is not None and not reseed:
                    continue
                if field.secret:
                    await self._upsert_secret(session, field.name, raw, "env")
                else:
                    await self._upsert_setting(session, field.name, raw, "env")
                seeded += 1
            await session.commit()
        return seeded

    # ── UI-facing (masked) listings + status ─────────────────────────────────
    async def list_config(self) -> list[dict[str, Any]]:
        async with self._sessionmaker() as session:
            secrets = {s.name: s for s in (await session.execute(select(Secret))).scalars()}
            settings = {s.name: s for s in (await session.execute(select(Setting))).scalars()}
        out: list[dict[str, Any]] = []
        for field in catalog.CATALOG:
            entry: dict[str, Any] = {
                "name": field.name,
                "step": field.step.value,
                "secret": field.secret,
                "required": field.required,
                "help": field.help,
                "choices": list(field.choices),
            }
            if field.secret:
                stored = secrets.get(field.name)
                entry["set"] = stored is not None
                entry["last_four"] = stored.last_four if stored else None
                entry["source"] = stored.source if stored else None
            else:
                stored_setting = settings.get(field.name)
                entry["value"] = stored_setting.value if stored_setting else field.default
                entry["source"] = stored_setting.source if stored_setting else "default"
            out.append(entry)
        return out

    async def status(self) -> dict[str, Any]:
        resolved = await self.resolved()
        steps = [
            {
                "step": s.step.value,
                "complete": s.complete,
                "missing": s.missing,
                "verifiable": s.verifiable,
            }
            for s in catalog.step_status(resolved)
        ]
        issues = catalog.validate_config(resolved)
        return {"steps": steps, "issues": issues, "complete": not issues}

    # ── import / export ──────────────────────────────────────────────────────
    async def export_env(self) -> str:
        resolved = await self.resolved()
        lines = ["# Exported by DEM. Contains plaintext secrets — handle carefully.", ""]
        for field in catalog.CATALOG:
            value = resolved.get(field.name, "")
            if value:
                lines.append(f"{field.env}={value}")
        return "\n".join(lines) + "\n"

    async def export_bundle(self, passphrase: str) -> bytes:
        secrets: dict[str, str] = {}
        settings: dict[str, str] = {}
        async with self._sessionmaker() as session:
            for secret in (await session.execute(select(Secret))).scalars():
                secrets[secret.name] = self._box.decrypt(secret.ciphertext)
            for setting in (await session.execute(select(Setting))).scalars():
                settings[setting.name] = setting.value
        return encrypt_bundle({"secrets": secrets, "settings": settings}, passphrase)

    async def import_bundle(self, blob: bytes, passphrase: str) -> int:
        payload = decrypt_bundle(blob, passphrase)
        secrets = payload.get("secrets")
        settings = payload.get("settings")
        imported = 0
        async with self._sessionmaker() as session:
            for name, value in (secrets if isinstance(secrets, dict) else {}).items():
                if name in catalog.SECRET_NAMES:
                    await self._upsert_secret(session, name, str(value), "import")
                    imported += 1
            for name, value in (settings if isinstance(settings, dict) else {}).items():
                if name in catalog.BY_NAME and not catalog.BY_NAME[name].secret:
                    await self._upsert_setting(session, name, str(value), "import")
                    imported += 1
            await session.commit()
        return imported
