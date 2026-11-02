"""
schemas.py — API가 주고받는 데이터 형식 (Pydantic)

선택지가 정해진 값은 Literal로 묶어, 정해지지 않은 값이 들어오면 FastAPI가 422로 거절한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Category = Literal["상의", "하의", "아우터", "신발", "가방", "액세서리", "미분류"]
Season = Literal["사계절", "봄", "여름", "가을", "겨울"]
Style = Literal["캐주얼", "포멀", "스트릿", "미니멀", "빈티지", "스포티"]
Mood = Literal["신남", "편안함", "진지함", "설렘", "피곤함", "자신감"]
Occasion = Literal["출근", "데이트", "친구 약속", "면접", "외출", "운동", "여행", "집에서"]
Gender = Literal["선택 안 함", "남성", "여성"]
BodyType = Literal["슬림", "보통", "근육형", "통통"]
SkinTone = Literal["밝음", "중간", "어두움"]
PersonalColor = Literal["모름", "봄 웜톤", "여름 쿨톤", "가을 웜톤", "겨울 쿨톤"]


# ── 회원 ──────────────────────────────────────────────
class SignupRequest(BaseModel):
    email: EmailStr
    nickname: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class Profile(BaseModel):
    gender: Gender = "선택 안 함"
    body_type: BodyType = "보통"
    skin_tone: SkinTone = "중간"
    personal_color: PersonalColor = "모름"
    height_cm: int | None = Field(default=None, ge=100, le=230)
    weight_kg: int | None = Field(default=None, ge=25, le=250)


class UserOut(Profile):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    nickname: str


class TokenResponse(BaseModel):
    token: str
    user: UserOut


# ── 옷장 ──────────────────────────────────────────────
class ItemUpdate(BaseModel):
    category: Category | None = None
    season: Season | None = None
    style: Style | None = None
    color: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=50)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category: str
    season: str
    style: str
    color: str
    name: str
    photo_url: str | None = None
    last_worn_at: datetime | None = None


class WearRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=10)


# ── 날씨 ──────────────────────────────────────────────
class Weather(BaseModel):
    temp: float = Field(ge=-40, le=50)
    desc: str = Field(default="맑음", max_length=20)
    humidity: int = Field(default=50, ge=0, le=100)
    feels_like: float | None = None
    place: str | None = None


# ── 추천 ──────────────────────────────────────────────
class RecommendRequest(BaseModel):
    weather: Weather
    mood: Mood = "편안함"
    occasions: list[Occasion] = Field(default_factory=list, max_length=4)
    style: Style = "캐주얼"


class Pick(BaseModel):
    slot: Literal["top", "bottom", "outer", "shoes", "accessory"]
    item: ItemOut


class Candidate(BaseModel):
    item_id: int
    score: int
    reason: str


class RecommendResponse(BaseModel):
    text: str                       # LLM이 쓴 추천 이유와 스타일링 팁
    picks: list[Pick]               # 옷장에서 고른 아이템 (옷장이 비었으면 빈 목록)
    candidates: list[Candidate]     # 규칙 점수로 미리 고른 카테고리별 1순위 (LLM에 ★로 전달)
    used_wardrobe: bool
