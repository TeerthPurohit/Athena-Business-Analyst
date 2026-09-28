"""Account registration and JWT issuance for the Athena workspace."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.business_analyst.api.security import _decode_org_claim
from agents.business_analyst.models import BaUser
from models.engine import get_async_session

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])
password_hash = PasswordHash.recommended()
TOKEN_LIFETIME = timedelta(hours=12)


async def get_auth_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_async_session() as session:
        yield session


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    org_id: str
    name: str
    email: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


def _user_response(user: BaUser) -> UserResponse:
    return UserResponse(id=user.id, org_id=user.org_id, name=user.name, email=user.email)


def _issue_token(user: BaUser) -> str:
    secret = os.getenv("JWT_ACCESS_SECRET")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=500, detail="JWT_ACCESS_SECRET must be set to at least 32 bytes.")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user.id, "org_id": user.org_id, "iat": now, "exp": now + TOKEN_LIFETIME},
        secret,
        algorithm="HS256",
    )


@auth_router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_auth_db)) -> AuthResponse:
    email = str(body.email).strip().lower()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required.")
    # Check configuration before writing an account that cannot sign in.
    if not os.getenv("JWT_ACCESS_SECRET") or len(os.environ["JWT_ACCESS_SECRET"].encode("utf-8")) < 32:
        raise HTTPException(status_code=500, detail="JWT_ACCESS_SECRET must be set to at least 32 bytes.")
    user = BaUser(
        id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), email=email, name=name,
        password_hash=password_hash.hash(body.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return AuthResponse(access_token=_issue_token(user), user=_user_response(user))


@auth_router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_auth_db)) -> AuthResponse:
    email = str(body.email).strip().lower()
    user = await session.scalar(select(BaUser).where(BaUser.email == email))
    if user is None or not password_hash.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return AuthResponse(access_token=_issue_token(user), user=_user_response(user))


@auth_router.get("/me", response_model=UserResponse)
async def me(request: Request, session: AsyncSession = Depends(get_auth_db)) -> UserResponse:
    org_id = await _decode_org_claim(request)
    token = request.headers["Authorization"].split(" ", 1)[1].strip()
    payload = jwt.decode(token, os.environ["JWT_ACCESS_SECRET"], algorithms=["HS256"])
    user_id = payload.get("sub")
    user = await session.get(BaUser, user_id) if isinstance(user_id, str) else None
    if user is None or user.org_id != org_id:
        raise HTTPException(status_code=401, detail="Account is no longer available.")
    return _user_response(user)
