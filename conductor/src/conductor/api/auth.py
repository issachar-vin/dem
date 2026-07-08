from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from conductor.auth import AuthStore

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


def _auth(request: Request) -> AuthStore:
    store: AuthStore = request.app.state.auth
    return store


def require_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Gate for the management API: a valid Bearer session token → the username, else 401."""
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    username = _auth(request).verify_token(token) if token else None
    if username is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return username


@router.get("/status")
async def auth_status(request: Request) -> dict[str, bool]:
    return {"initialized": await _auth(request).is_initialized()}


@router.post("/register")
async def register(body: Credentials, request: Request) -> dict[str, str]:
    auth = _auth(request)
    if await auth.is_initialized():
        raise HTTPException(status_code=409, detail="An admin account already exists.")
    await auth.create_admin(body.username, body.password)
    return {"token": auth.issue_token(body.username), "username": body.username}


@router.post("/login")
async def login(body: Credentials, request: Request) -> dict[str, str]:
    auth = _auth(request)
    if not await auth.verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return {"token": auth.issue_token(body.username), "username": body.username}
