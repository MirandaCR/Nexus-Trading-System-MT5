"""
JWT authentication middleware + PIN verification for sensitive ops.

Tokens are symmetric HS256 signed with ``NEXUS_JWT_SECRET``.
The kill-switch PIN is read from ``NEXUS_KILL_PIN`` (default "000000").
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

_JWT_SECRET = os.getenv("NEXUS_JWT_SECRET", "nexus-dev-secret-change-me")
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = int(os.getenv("NEXUS_JWT_EXPIRE_HOURS", "24"))

KILL_PIN = os.getenv("NEXUS_KILL_PIN", "000000")


class TokenPayload(BaseModel):
    user_id: str
    exp: datetime


# ── Token helpers ────────────────────────────────────────────────────────────

def create_token(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> str:
    """Return the ``user_id`` or raise 401."""
    try:
        data = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        user_id: str | None = data.get("user_id")
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if authorization is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid auth scheme")
    return verify_token(token)


CurrentUser = Annotated[str, Depends(get_current_user)]


def verify_pin(pin: str) -> None:
    """Raise 403 if the supplied PIN does not match."""
    if pin != KILL_PIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid PIN")
