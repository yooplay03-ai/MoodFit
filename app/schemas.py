"""
schemas.py — API가 주고받는 데이터 형식 (Pydantic)

선택지가 정해진 값은 Literal로 묶어, 정해지지 않은 값이 들어오면 FastAPI가 422로 거절한다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Category = Literal["상의", "하의", "아우터", "신발", "가방", "액세서리", "미분류"]
Season = Literal["사계절", "봄", "여름", "가을", "겨울"]
# 사진만으로 계절을 맞히기 어려운 종류 — 실제 사진 평가에서 하의는 8벌 중 4벌만 맞았다(상의·아우터는 13벌 중 12벌).
# 치마·바지는 모양이 계절과 거의 상관없고 원단 두께가 사진에 잘 드러나지 않기 때문. 이 종류는 사용자에게 확인을 받는다.
SEASON_CHECK_CATEGORIES = {"하의"}
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
    season_check: bool = False           # AI가 추측한 계절이라 사용자 확인이 필요함
    source: str | None = None            # None=직접 올린 사진, "link"=상품 링크, "catalog:<id>"=기본 옷 예시 사진
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


class OutfitOut(BaseModel):
    picks: list[Pick]
    score: int                      # 0~100
    breakdown: dict[str, int]       # 항목별 0~100 (날씨·상황·색 조합·기분 스타일·새로움)
    reasons: list[str]              # 규칙이 만든 이유 (LLM 없이도 보여 줄 수 있음)
    comment: str = ""               # LLM이 쓴 설명


class RecommendResponse(BaseModel):
    text: str                       # 옷장이 비었을 때 LLM의 일반 코디 조언, 옷장이 있으면 짧은 총평
    outfits: list[OutfitOut]        # 점수 상위 코디 (최대 3개)
    picks: list[Pick]               # outfits[0]의 옷 (이전 화면과의 호환용)
    used_wardrobe: bool
    llm_used: bool                  # LLM 설명을 받았는지 (키가 없거나 실패하면 규칙 이유만)


class FeedbackRequest(BaseModel):
    item_ids: dict[Literal["top", "bottom", "outer", "shoes", "accessory"], int]
    weather: Weather
    mood: Mood = "편안함"
    occasions: list[Occasion] = Field(default_factory=list, max_length=4)
    style: Style = "캐주얼"
    source: Literal["model", "random"] = "model"
    rating: Literal[1, -1]


class SampleResponse(BaseModel):
    outfit: OutfitOut | None        # 평가용 무작위 코디 (상의·하의가 없으면 None)


class CatalogItem(BaseModel):
    id: str
    name: str
    category: str
    color: str
    season: str
    style: str
    image_url: str | None           # 품질 기준을 통과한 사진이 없으면 None → 화면에서 옷 그림
    photographer: str | None = None
    photographer_url: str | None = None
    photo_page: str | None = None
    source: str | None = None       # 사진 출처 (Pixabay, Pexels)


class CatalogAddRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=60)


class LinkImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=1000)


class LinkImportResponse(BaseModel):
    title: str
    image: str                      # data:image/jpeg;base64,… (화면에서 바로 미리보기·분석에 씀)
