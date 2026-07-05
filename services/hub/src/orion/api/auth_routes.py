from services.shared.environment import get_env
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt

from orion.api.security import (
    hash_password,
    verify_password,
    create_access_token,
    JWT_SECRET_KEY,
    ALGORITHM
)
from orion.kernel.registry import get_user_by_username, create_user

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

class AuthRequest(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserProfile(BaseModel):
    username: str
    is_active: bool

async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> str:
    admin_key = get_env("ADMIN_API_KEY")
    provided_admin_key = request.headers.get("X-Admin-Key")
    
    if provided_admin_key is not None:
        if not admin_key or provided_admin_key == admin_key:
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
    
    try:
        payload = jwt.decode(token_str, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
    return {"message": "User created successfully"}


@router.post("/login", response_model=Token)
async def login(request: AuthRequest):
    user = await get_user_by_username(request.username)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    access_token = create_access_token(data={"sub": user["id"]})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserProfile)
async def read_users_me(current_user: str = Depends(get_current_user)):
    user = await get_user_by_username(current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserProfile(username=user["id"], is_active=user["is_active"])
