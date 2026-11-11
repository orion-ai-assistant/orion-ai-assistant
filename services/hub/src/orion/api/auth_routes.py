from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator

from orion.api.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    hash_password,
    verify_password,
    valid_admin_key,
    require_admin,
)
from orion.kernel.registry import (
    get_user_by_username, create_user, set_user_active, set_user_approval,
    set_user_password, set_user_avatar, get_user_avatar,
)
from orion.api.login_limits import limit_login, limit_attempts
from orion.api import invitations, sessions
from orion.api.access_boundary import is_local
import asyncio
import time

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

class AuthRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value

class Token(BaseModel):
    access_token: str
    token_type: str

class RegisterRequest(AuthRequest):
    invitation_token: str | None = Field(default=None, min_length=20, max_length=128)

class InvitationPreview(BaseModel):
    invitation_token: str = Field(min_length=20, max_length=128)

class UserProfile(BaseModel):
    username: str
    is_active: bool
    # Changes whenever the picture does, so a client knows to fetch it again.
    avatar_version: str | None = None


def profile_of(user: dict) -> UserProfile:
    updated = user.get("avatar_updated_at")
    return UserProfile(username=user["id"], is_active=user["is_active"],
                       avatar_version=updated.isoformat() if updated else None)

async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> str:
    provided_admin_key = request.headers.get("X-Admin-Key") or request.query_params.get("admin_key")

    if provided_admin_key is not None:
        if valid_admin_key(provided_admin_key):
            return "global"

    token_str = None
    if credentials:
        token_str = credentials.credentials
    else:
        token_str = request.query_params.get("token")

    if not token_str:
        if getattr(request.state, 'owner_scope', None) == 'admin' and request.url.path.startswith(('/api/v1/admin/', '/api/v1/browser', '/api/v1/google-ai/session')):
            return 'global'
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await authenticate_token(token_str, request)


async def authenticate_token(token_str: str, connection=None) -> str:
    """Every entry point (HTTP, SSE, WebSocket; local, public, mobile) authenticates accounts here."""
    claims = sessions.decode(token_str)
    if (connection is not None and getattr(connection.app.state, 'mobile', False)
            and claims.get('device_id') != getattr(connection.state, 'device_id', None)):
        raise sessions.ended('Account token is not bound to this device')
    username = claims.get('sub')
    if claims.get('sid') is None:
        # A pre-session token from before this Hub version: valid until its own 24 h expiry.
        # The client exchanges it for a session at startup (POST /auth/session).
        # Such tokens lived 24 h; a later expiry was not issued here (a forged token).
        latest = time.time() + ACCESS_TOKEN_EXPIRE_MINUTES * 60 + 300
        if not isinstance(claims.get('exp'), (int, float)) or claims['exp'] > latest:
            raise sessions.ended()
        await require_active_user(username)
    else:
        await sessions.check(claims)
    return username


@router.post('/invitations/preview')
async def preview(payload: InvitationPreview, request: Request):
    await limit_attempts(request, 'invite-preview', request.client.host, 30)
    return await invitations.preview_invitation(payload.invitation_token)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=Token)
async def register(request: RegisterRequest, http_request: Request):
    await limit_attempts(http_request, 'registration', http_request.client.host, 10)
    local_registration = (not getattr(http_request.app.state, 'mobile', False)
                          and is_local(http_request.scope))
    # A phone paired with a QR from the Hub's PC may open one account, whenever it likes.
    device_id = getattr(http_request.state, 'device_id', None) if not local_registration else None
    if not request.invitation_token and not local_registration and not device_id:
        raise HTTPException(403, 'Hesap açmak için telefonu Hub’ın bilgisayarındaki QR ile bağla.')
    if request.username == "global":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reserved username."
        )

    # Validate before expensive password hashing; the transaction validates again under lock.
    if request.invitation_token:
        await invitations.preview_invitation(request.invitation_token)
    password_hash = await asyncio.to_thread(hash_password, request.password)
    if request.invitation_token:
        await invitations.redeem_invitation(request.invitation_token, request.username, password_hash)
    else:
        from orion.kernel.local_security import SecurityStore
        from orion.kernel.registry import create_local_user
        store = SecurityStore()
        # Taken before the account exists, so two requests cannot both use it; given back on failure.
        if device_id and not await asyncio.to_thread(store.take_registration, device_id):
            raise HTTPException(403, 'Bu telefonla zaten bir hesap açıldı. Başka bir hesap için yeni bir QR okut.')
        try:
            created = await create_local_user(request.username, password_hash)
        except RuntimeError as error:
            if device_id:
                await asyncio.to_thread(store.return_registration, device_id)
            raise HTTPException(503, 'Hesap veritabanına erişilemiyor.') from error
        if not created:
            if device_id:
                await asyncio.to_thread(store.return_registration, device_id)
            raise HTTPException(409, 'Bu kullanıcı adı zaten kullanılıyor.')
    from orion.kernel.router_accounts import provision_if_available
    await provision_if_available(request.username)
    return {'access_token': await sessions.issue({'id': request.username, 'password_hash': password_hash},
                                                 bound_device(http_request)),
            'token_type': 'bearer'}


@router.get('/registration')
async def registration(http_request: Request):
    """Whether this client may open an account now: the app shows or explains the form with it."""
    if not getattr(http_request.app.state, 'mobile', False) and is_local(http_request.scope):
        return {'allowed': True}
    device_id = getattr(http_request.state, 'device_id', None)
    if not device_id:
        return {'allowed': False}
    from orion.kernel.local_security import SecurityStore
    return {'allowed': await asyncio.to_thread(SecurityStore().may_register, device_id)}


@router.post("/login", response_model=Token)
async def login(request: AuthRequest, http_request: Request):
    await limit_login(http_request, request.username)
    user = await get_user_by_username(request.username)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    return {"access_token": await sessions.issue(user, bound_device(http_request)), "token_type": "bearer"}


def bound_device(request: Request) -> str | None:
    # A phone's account session belongs to its paired device; browsers have none.
    return request.state.device_id if getattr(request.app.state, 'mobile', False) else None


@router.post("/session", response_model=Token)
async def exchange_session(http_request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    """Replace a valid pre-session token with a session token, so an update logs nobody out."""
    if not credentials:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    username = await authenticate_token(credentials.credentials, http_request)
    if sessions.decode(credentials.credentials).get('sid') is not None:
        return {"access_token": credentials.credentials, "token_type": "bearer"}
    user = await get_user_by_username(username)
    if not user:
        raise sessions.ended()
    return {"access_token": await sessions.issue(user, bound_device(http_request)), "token_type": "bearer"}


@router.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if credentials:
        await sessions.revoke(credentials.credentials)
    return {"ok": True}


@router.get("/me", response_model=UserProfile)
async def read_users_me(current_user: str = Depends(get_current_user)):
    user = await get_user_by_username(current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return profile_of(user)


class UsernameChange(BaseModel):
    new_username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=72)


@router.post("/username")
async def change_username(payload: UsernameChange, http_request: Request,
                          current_user: str = Depends(get_current_user)):
    """The login name itself changes (see services/account_rename.py). Other sessions end; this device
    gets a new token and the new name."""
    from orion.api.services.account_rename import valid_username, rename_account
    from orion.kernel.registry import UsernameTaken
    await limit_attempts(http_request, 'username-change', current_user, 10)
    new = valid_username(payload.new_username)
    user = await get_user_by_username(current_user)
    if not user:
        raise sessions.ended()
    if not await asyncio.to_thread(verify_password, payload.password, user["password_hash"]):
        raise HTTPException(400, "Şifre yanlış.")
    if new == current_user:
        raise HTTPException(400, "Bu zaten kullanıcı adın.")
    try:
        await rename_account(http_request.app.state.redis, user, new)
    except UsernameTaken as exc:
        raise HTTPException(409, "Bu kullanıcı adı zaten kullanılıyor.") from exc
    except LookupError as exc:
        raise sessions.ended() from exc
    except RuntimeError as exc:
        raise HTTPException(503, "Hesap veritabanına erişilemiyor.") from exc
    token = await sessions.issue({'id': new, 'password_hash': user["password_hash"]}, bound_device(http_request))
    return {"access_token": token, "token_type": "bearer", "username": new}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=1, max_length=72)

    @field_validator("current_password", "new_password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


@router.post("/password", response_model=Token)
async def change_password(payload: PasswordChange, http_request: Request,
                          current_user: str = Depends(get_current_user)):
    """Every other session of the account ends (they carry the old password's
    credential); this device gets a new token and stays signed in."""
    await limit_attempts(http_request, 'password-change', current_user, 10)
    user = await get_user_by_username(current_user)
    if not user:
        raise sessions.ended()
    if not await asyncio.to_thread(verify_password, payload.current_password, user["password_hash"]):
        raise HTTPException(400, "Mevcut şifre yanlış.")
    password_hash = await asyncio.to_thread(hash_password, payload.new_password)
    try:
        if not await set_user_password(current_user, password_hash):
            raise sessions.ended()
    except RuntimeError as exc:
        raise HTTPException(503, "Hesap veritabanına erişilemiyor.") from exc
    return {"access_token": await sessions.issue({'id': current_user, 'password_hash': password_hash},
                                                 bound_device(http_request)),
            "token_type": "bearer"}


AVATAR_LIMIT = 512 * 1024
AVATAR_TYPES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}


class AvatarUpload(BaseModel):
    mime_type: str
    # Base64; the apps send a small square they already scaled down.
    data: str = Field(max_length=AVATAR_LIMIT * 4 // 3 + 8)


@router.put("/avatar", response_model=UserProfile)
async def upload_avatar(payload: AvatarUpload, current_user: str = Depends(get_current_user)):
    import base64
    import binascii
    try:
        raw = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Görsel okunamadı.") from exc
    magic = AVATAR_TYPES.get(payload.mime_type)
    if (not magic or not raw or len(raw) > AVATAR_LIMIT or not raw.startswith(magic)
            or (payload.mime_type == "image/webp" and raw[8:12] != b"WEBP")):
        raise HTTPException(400, "Profil fotoğrafı PNG, JPEG veya WebP olmalı ve 512 KB'ı geçmemeli.")
    try:
        if not await set_user_avatar(current_user, raw, payload.mime_type):
            raise HTTPException(404, "User not found")
    except RuntimeError as exc:
        raise HTTPException(503, "Hesap veritabanına erişilemiyor.") from exc
    return profile_of(await get_user_by_username(current_user))


@router.delete("/avatar", response_model=UserProfile)
async def delete_avatar(current_user: str = Depends(get_current_user)):
    try:
        if not await set_user_avatar(current_user, None, None):
            raise HTTPException(404, "User not found")
    except RuntimeError as exc:
        raise HTTPException(503, "Hesap veritabanına erişilemiyor.") from exc
    return profile_of(await get_user_by_username(current_user))


@router.get("/avatar")
async def read_avatar(current_user: str = Depends(get_current_user)):
    try:
        avatar = await get_user_avatar(current_user)
    except RuntimeError as exc:
        raise HTTPException(503, "Hesap veritabanına erişilemiyor.") from exc
    if avatar is None:
        raise HTTPException(404, "Profil fotoğrafı yok.")
    mime, raw = avatar
    return Response(content=raw, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


async def require_active_user(username: str) -> None:
    if not isinstance(username, str) or username == "global":
        raise sessions.ended("Invalid account")
    user = await get_user_by_username(username)
    if not user or not user.get("is_active", False):
        raise sessions.ended("Account inactive or awaiting owner approval")


class ApprovalRequest(BaseModel):
    is_active: bool = True


@router.put("/users/{username}/approval")
async def approve_user(username: str, payload: ApprovalRequest, request: Request):
    require_admin(request)
    if username == "global":
        raise HTTPException(404, "User not found")
    try:
        updated = await set_user_active(username, payload.is_active)
    except RuntimeError as exc:
        raise HTTPException(503, "User database unavailable") from exc
    if not updated:
        raise HTTPException(404, "User not found")
    return {"username": username, "is_active": payload.is_active}


class PatchApprovalRequest(BaseModel):
    approved: bool


@router.patch("/users/{username}/approval")
async def patch_user_approval(username: str, payload: PatchApprovalRequest, request: Request):
    require_admin(request)
    if username == "global":
        raise HTTPException(404, "User not found")
    try:
        updated = await set_user_approval(username, payload.approved)
    except RuntimeError as exc:
        raise HTTPException(503, "User database unavailable") from exc
    if not updated:
        raise HTTPException(404, "User not found")
    return {"username": username, "is_active": payload.approved}
