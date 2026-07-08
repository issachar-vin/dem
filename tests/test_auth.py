from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.api import auth as auth_api
from conductor.auth import AuthStore
from conductor.models import User


# ── store ──────────────────────────────────────────────────────────────────────
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


def test_token_round_trip(auth: AuthStore) -> None:
    token = auth.issue_token("admin")
    assert auth.verify_token(token) == "admin"


def test_verify_token_rejects_garbage(auth: AuthStore) -> None:
    assert auth.verify_token("not-a-token") is None


# ── API ────────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(auth: AuthStore) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.auth = auth
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_status_reports_initialization(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/auth/status")).json() == {"initialized": False}
    await api.post("/api/auth/register", json={"username": "admin", "password": "pw"})
    assert (await api.get("/api/auth/status")).json() == {"initialized": True}


async def test_register_once_then_409(api: httpx.AsyncClient) -> None:
    first = await api.post(
        "/api/auth/register", json={"username": "admin", "password": "pw"}
    )
    assert first.status_code == 200
    assert first.json()["token"]
    second = await api.post(
        "/api/auth/register", json={"username": "other", "password": "pw"}
    )
    assert second.status_code == 409


async def test_login_good_and_bad(api: httpx.AsyncClient) -> None:
    await api.post("/api/auth/register", json={"username": "admin", "password": "pw"})
    good = await api.post(
        "/api/auth/login", json={"username": "admin", "password": "pw"}
    )
    assert good.status_code == 200
    assert good.json()["token"]
    bad = await api.post(
        "/api/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert bad.status_code == 401
