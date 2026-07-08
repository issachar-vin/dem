from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import User


class AuthStore:
    """Console operator accounts. Passwords are argon2-hashed (one-way). The NiceGUI console signs
    its own session cookie (keyed by DEM_SECRET_KEY), so this store no longer issues tokens."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._hasher = PasswordHasher()

    async def is_initialized(self) -> bool:
        async with self._sessionmaker() as session:
            count = await session.scalar(select(func.count()).select_from(User))
            return bool(count)

    async def create_admin(self, username: str, password: str) -> None:
        async with self._sessionmaker() as session:
            session.add(User(username=username, password_hash=self._hasher.hash(password)))
            await session.commit()

    async def verify_credentials(self, username: str, password: str) -> bool:
        async with self._sessionmaker() as session:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
        if user is None:
            return False
        try:
            self._hasher.verify(user.password_hash, password)
        except VerifyMismatchError:
            return False
        return True
