"""
models.py — DB 테이블 정의

users            회원 + 스타일 프로필 (프로필은 회원당 하나라 같은 테이블에 둔다)
clothing_items   옷장 아이템 (회원 1명 : 옷 N벌)
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(200))   # 비밀번호 원문은 저장하지 않는다
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # 스타일 프로필 — 추천 프롬프트에 들어간다
    gender: Mapped[str] = mapped_column(String(10), default="선택 안 함")
    body_type: Mapped[str] = mapped_column(String(10), default="보통")
    skin_tone: Mapped[str] = mapped_column(String(10), default="중간")
    personal_color: Mapped[str] = mapped_column(String(10), default="모름")
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)

    items: Mapped[list["ClothingItem"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class ClothingItem(Base):
    __tablename__ = "clothing_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(10))           # 상의/하의/아우터/신발/가방/액세서리/미분류
    season: Mapped[str] = mapped_column(String(10), default="사계절")
    season_by_user: Mapped[bool] = mapped_column(Boolean, default=False)   # 사용자가 직접 고른 계절이면 AI가 덮어쓰지 않는다
    style: Mapped[str] = mapped_column(String(10), default="캐주얼")
    color: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(String(50), default="")
    photo: Mapped[str | None] = mapped_column(String(100), nullable=True)   # media 폴더 안의 파일 이름
    last_worn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="items")
