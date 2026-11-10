"""Portal login: password hashing, signed session cookie, and role checks."""

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import User
from database.session import get_db_session

COOKIE_NAME = "voice_agent_session"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_DEV_SECRET = "development-only-secret-not-for-production"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), **_SCRYPT), expected)
    except (ValueError, TypeError):
        return False


def _secret() -> str:
    s = get_settings()
    if s.AUTH_SECRET:
        return s.AUTH_SECRET
    if s.is_production:
        raise HTTPException(503, "AUTH_SECRET must be configured in production")
    return _DEV_SECRET


def create_session_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user.id, "iat": now, "exp": now + timedelta(hours=get_settings().AUTH_SESSION_HOURS)},
                      _secret(), algorithm="HS256")


async def _user_from_cookie(token: Optional[str], db: AsyncSession) -> Optional[User]:
    if not token:
        return None
    try:
        user_id = jwt.decode(token, _secret(), algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None
    user = await db.get(User, user_id)
    return user if user is not None and user.is_active else None


async def current_user(voice_agent_session: Optional[str] = Cookie(default=None),
                       db: AsyncSession = Depends(get_db_session)) -> User:
    user = await _user_from_cookie(voice_agent_session, db)
    if user is None:
        raise HTTPException(401, "Please sign in")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def client_scope(client_id: Optional[str] = Query(default=None), user: User = Depends(current_user)) -> str:
    """The client this request may read and change. A client user can only ever get their own;
    an admin names the client they are viewing."""
    if user.role == "admin":
        if not client_id:
            raise HTTPException(400, "client_id is required")
        return client_id
    if not user.client_id:
        raise HTTPException(403, "This login has no client")
    return user.client_id


async def require_internal(x_api_key: Optional[str] = Header(default=None),
                           voice_agent_session: Optional[str] = Cookie(default=None),
                           db: AsyncSession = Depends(get_db_session)) -> None:
    """Operator endpoints: a configured API key or an admin login. Open only in development with no keys set."""
    s = get_settings()
    if x_api_key and x_api_key in s.api_keys:
        return
    user = await _user_from_cookie(voice_agent_session, db)
    if user is not None and user.role == "admin":
        return
    if not s.api_keys and not s.is_production:
        return
    raise HTTPException(401, "Invalid or missing credentials")
