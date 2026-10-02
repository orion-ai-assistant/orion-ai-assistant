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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await authenticate_token(token_str)


async def authenticate_token(token_str: str) -> str:
    try:
        payload = jwt.decode(token_str, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        await require_active_user(username)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Could not validate credentials", headers={"WWW-Authenticate": "Bearer"}) from exc
    return username


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(request: AuthRequest):
    if request.username == "global":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reserved username."
        )

    existing_user = await get_user_by_username(request.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    hashed_pw = hash_password(request.password)
    success = await create_user(request.username, hashed_pw)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )
    return {"message": "Registration pending owner approval", "is_active": False}


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

    access_token = create_access_token(data={"sub": user["id"]})
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


LOGIN_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


async def limit_login(request: Request, username: str) -> None:
    import hashlib
    import logging
    # Trust the transport address, never user-supplied forwarding headers.
    ip = request.client.host if request.client else "unknown"
    redis = request.app.state.redis
    for scope, value, threshold in (("account", username, 5), ("ip", ip, 10)):
        key = "auth:login:" + scope + ":" + hashlib.sha256(value.encode()).hexdigest()
        try:
            count = await redis.eval(LOGIN_LIMIT_SCRIPT, 1, key, 60)
        except Exception as exc:
            logging.getLogger(__name__).exception("Login limiter unavailable")
            raise HTTPException(503, "Login temporarily unavailable") from exc
        if int(count) > threshold:
            raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "60"})


class ApprovalRequest(BaseModel):
    is_active: bool = True


@router.put("/users/{username}/approval")
async def approve_user(username: str, payload: ApprovalRequest, request: Request):
    if not valid_admin_key(request.headers.get("X-Admin-Key")):
        raise HTTPException(401, "Unauthorized")
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
