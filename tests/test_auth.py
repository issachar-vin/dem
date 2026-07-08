from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.auth import AuthStore
from conductor.models import User


async def test_initialized_flips_after_admin_created(auth: AuthStore) -> None:
    assert await auth.is_initialized() is False
    await auth.create_admin("admin", "pw")
    assert await auth.is_initialized() is True


async def test_verify_credentials(auth: AuthStore) -> None:
    await auth.create_admin("admin", "pw")
    assert await auth.verify_credentials("admin", "pw") is True
    assert await auth.verify_credentials("admin", "wrong") is False
    assert await auth.verify_credentials("nobody", "pw") is False


async def test_password_is_hashed_not_stored_plaintext(
    auth: AuthStore, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await auth.create_admin("admin", "pw")
    async with sessionmaker() as session:
        user = (await session.execute(select(User))).scalar_one()
    assert user.password_hash != "pw"
    assert user.password_hash.startswith("$argon2")
