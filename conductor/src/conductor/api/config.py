import base64
import binascii
import json

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from conductor import catalog, verify
from conductor.api.auth import require_user
from conductor.store import ConfigStore

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_user)])


class SecretBody(BaseModel):
    value: str


class SettingBody(BaseModel):
    value: str


class ExportBundleBody(BaseModel):
    passphrase: str


class ImportBundleBody(BaseModel):
    blob_b64: str
    passphrase: str


def _store(request: Request) -> ConfigStore:
    store: ConfigStore = request.app.state.store
    return store


@router.get("")
async def list_config(request: Request) -> list[dict[str, object]]:
    return await _store(request).list_config()


@router.get("/status")
async def config_status(request: Request) -> dict[str, object]:
    return await _store(request).status()


@router.put("/secret/{name}")
async def set_secret(
    name: str, body: SecretBody, request: Request, user: str = Depends(require_user)
) -> dict[str, str]:
    if name not in catalog.SECRET_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown secret: {name}")
    await _store(request).set_secret(name, body.value, source=user)
    return {"name": name, "status": "set"}


@router.put("/setting/{name}")
async def set_setting(
    name: str, body: SettingBody, request: Request, user: str = Depends(require_user)
) -> dict[str, str]:
    field = catalog.BY_NAME.get(name)
    if field is None or field.secret:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {name}")
    if field.choices and body.value not in field.choices:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be one of {list(field.choices)}, got {body.value!r}",
        )
    await _store(request).set_setting(name, body.value, source=user)
    return {"name": name, "status": "set"}


@router.post("/test/{service}")
async def test_connection(service: str, request: Request) -> dict[str, object]:
    resolved = await _store(request).resolved()
    if service == "claude":
        result = await verify.verify_claude(
            oauth_token=resolved.get("claude_code_oauth_token") or None,
            api_key=resolved.get("anthropic_api_key") or None,
        )
    elif service == "plane":
        result = await verify.verify_plane(
            base_url=resolved.get("plane_base_url", ""),
            api_key=resolved.get("plane_api_key", ""),
            workspace_slug=resolved.get("plane_workspace_slug", ""),
        )
    elif service == "github":
        result = await verify.verify_github(token=resolved.get("github_token", ""))
    else:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service}")
    return {"ok": result.ok, "detail": result.detail}


@router.get("/export.env")
async def export_env(request: Request) -> Response:
    body = await _store(request).export_env()
    return Response(content=body, media_type="text/plain")


@router.post("/export-bundle")
async def export_bundle(body: ExportBundleBody, request: Request) -> dict[str, str]:
    blob = await _store(request).export_bundle(body.passphrase)
    return {"blob_b64": base64.b64encode(blob).decode()}


@router.post("/import-bundle")
async def import_bundle(body: ImportBundleBody, request: Request) -> dict[str, int]:
    try:
        blob = base64.b64decode(body.blob_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed bundle encoding.") from exc
    try:
        imported = await _store(request).import_bundle(blob, body.passphrase)
    except (InvalidToken, binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Wrong passphrase or corrupt bundle.") from exc
    return {"imported": imported}
