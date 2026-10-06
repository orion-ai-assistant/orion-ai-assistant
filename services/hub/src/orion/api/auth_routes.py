from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
import jwt

from orion.api.security import (
    hash_password,
    verify_password,
    create_access_token,
    JWT_SECRET_KEY,
    valid_admin_key,
    require_admin,
    ALGORITHM
)
from orion.kernel.registry import get_user_by_username, create_user, set_user_active, set_user_approval
from orion.api.login_limits import limit_login, limit_attempts
from orion.api import invitations
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

    if getattr(request.app.state, 'mobile', False):
        try:
            payload = jwt.decode(token_str, JWT_SECRET_KEY, algorithms=[ALGORITHM])
            if payload.get('device_id') != getattr(request.state, 'device_id', None):
                raise HTTPException(401, 'Account token is not bound to this device')
        except jwt.PyJWTError as error:
            raise HTTPException(401, 'Invalid account token') from error
    return await authenticate_token(token_str)


async def authenticate_token(token_str: str) -> str:
    try:
        payload = jwt.decode(token_str, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        await require_active_user(username)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Could not validate credentials", headers={"WWW-Authenticate": "Bearer"}) from exc
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
        await invitations.redeem_invitation(request.invitation_token, request.username,
                                            await asyncio.to_thread(hash_password, request.password))
    else:
        from orion.kernel.registry import create_local_user
        try:
            created = await create_local_user(request.username, await asyncio.to_thread(hash_password, request.password))
        except RuntimeError as error:
            raise HTTPException(503, 'Hesap veritabanına erişilemiyor.') from error
        if not created:
            raise HTTPException(409, 'Bu kullanıcı adı zaten kullanılıyor.')
    claims = {'sub': request.username}
    if getattr(http_request.app.state, 'mobile', False):
        claims['device_id'] = http_request.state.device_id
    return {'access_token': create_access_token(claims), 'token_type': 'bearer'}


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

    claims = {"sub": user["id"]}
    if getattr(http_request.app.state, 'mobile', False):
        claims['device_id'] = http_request.state.device_id
    access_token = create_access_token(data=claims)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserProfile)
async def read_users_me(current_user: str = Depends(get_current_user)):
    user = await get_user_by_username(current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfile(username=user["id"], is_active=user["is_active"])


async def require_active_user(username: str) -> None:
    if not isinstance(username, str) or username == "global":
        raise HTTPException(401, "Invalid account")
    user = await get_user_by_username(username)
    if not user or not user.get("is_active", False):
        raise HTTPException(401, "Account inactive or awaiting owner approval")


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
