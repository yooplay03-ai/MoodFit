"""
security.py — 비밀번호 해시와 로그인 토큰(JWT)

- 비밀번호: 사용자마다 다른 salt를 붙여 PBKDF2-SHA256으로 20만 번 해시한 값만 저장한다.
  DB가 유출돼도 원래 비밀번호를 바로 알 수 없고, 같은 비밀번호라도 저장값이 다르다.
- 토큰: 로그인하면 사용자 id와 만료 시각을 담은 JWT를 서명해 내려주고,
  이후 요청은 Authorization: Bearer <토큰> 헤더로 누구인지 확인한다.
"""

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User

ITERATIONS = 200_000
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    return hmac.compare_digest(digest, expected)   # 비교 시간으로 정답을 추측하지 못하게 한다


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=ALGORITHM)


bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """로그인이 필요한 API에 붙이는 의존성. 토큰이 없거나 틀리면 401."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    if cred is None:
        raise unauthorized
    try:
        payload = jwt.decode(cred.credentials, config.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user
