"""회원가입 · 로그인 · 내 정보와 프로필"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import LoginRequest, Profile, SignupRequest, TokenResponse, UserOut
from app.security import create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["회원"])


@router.post("/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    email = req.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 가입된 이메일입니다.")
    user = User(email=email, nickname=req.nickname.strip(), password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    return TokenResponse(token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == req.email.lower()))
    # 이메일이 없는 경우와 비밀번호가 틀린 경우를 같은 메시지로 답해, 가입 여부를 알 수 없게 한다
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다.")
    return TokenResponse(token=create_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.put("/me/profile", response_model=UserOut)
def update_profile(profile: Profile, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for key, value in profile.model_dump().items():
        setattr(user, key, value)
    db.commit()
    return user
