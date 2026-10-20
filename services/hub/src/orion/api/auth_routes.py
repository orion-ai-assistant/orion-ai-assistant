from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator

from orion.api.security import (
    hash_password,
    verify_password,
    valid_admin_key,
    require_admin,
)
from orion.kernel.registry import get_user_by_username, create_user, set_user_active, set_user_approval
from orion.api.login_limits import limit_login, limit_attempts
from orion.api import invitations, sessions
from orion.api.access_boundary import is_local
import asyncio

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
        if 'exp' not in claims:
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
    if not request.invitation_token and not local_registration:
        raise HTTPException(403, 'Hesap oluşturmak için PC sahibinden davet bağlantısı alın.')
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
        from orion.kernel.registry import create_local_user
        try:
            created = await create_local_user(request.username, password_hash)
        except RuntimeError as error:
            raise HTTPException(503, 'Hesap veritabanına erişilemiyor.') from error
        if not created:
            raise HTTPException(409, 'Bu kullanıcı adı zaten kullanılıyor.')
    from orion.kernel.router_accounts import provision_if_available
    await provision_if_available(request.username)
    return {'access_token': await sessions.issue({'id': request.username, 'password_hash': password_hash},
                                                 bound_device(http_request)),
            'token_type': 'bearer'}


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
    return UserProfile(username=user["id"], is_active=user["is_active"])


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
