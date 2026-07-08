import json

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import User

# Sessions are stateless: a Fernet token (keyed by DEM_SECRET_KEY) carrying the username, with
# Fernet's own timestamp giving us expiry. A week keeps the console logged in across a work session.
TOKEN_TTL_SECONDS = 7 * 24 * 3600


class AuthStore:
    """Console operator accounts and stateless session tokens. Passwords are argon2-hashed
    (one-way); tokens are signed+encrypted with the DEM_SECRET_KEY root of trust."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], secret_key: str) -> None:
        self._sessionmaker = sessionmaker
        self._hasher = PasswordHasher()
        self._fernet = Fernet(secret_key.encode())

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

    def issue_token(self, username: str) -> str:
        return self._fernet.encrypt(json.dumps({"username": username}).encode()).decode()

    def verify_token(self, token: str) -> str | None:
        try:
            data = json.loads(self._fernet.decrypt(token.encode(), ttl=TOKEN_TTL_SECONDS))
        except (InvalidToken, ValueError):
            return None
        username = data.get("username")
        return username if isinstance(username, str) else None
