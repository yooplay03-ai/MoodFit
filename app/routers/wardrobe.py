"""
옷장 — 옷 등록(사진 업로드), 목록, 수정, 삭제, '오늘 이 코디 입음' 기록

사진은 브라우저에서 긴 변 800px JPEG로 줄여서 올리고, 서버는 media 폴더에 무작위 파일 이름으로 저장한다.
DB에는 파일 이름만 남긴다. (예전 버전은 사진을 브라우저 localStorage에 넣어 5MB 한도에 금방 찼다)
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import ClothingItem, User
from app.schemas import SEASON_CHECK_CATEGORIES, Category, ItemOut, ItemUpdate, Season, Style, WearRequest
from app.security import get_current_user

router = APIRouter(prefix="/api/wardrobe", tags=["옷장"])
log = logging.getLogger("moodfit")

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_ITEMS = 200


def cut_name(filename: str) -> str:
    """배경을 지운 사진의 파일 이름 (원본 옆에 저장)"""
    return f"{filename.rsplit('.', 1)[0]}_cut.jpg"


def to_out(item: ClothingItem) -> ItemOut:
    out = ItemOut.model_validate(item)
    out.season_check = not item.season_by_user and item.category in SEASON_CHECK_CATEGORIES
    if item.photo:
        cut = cut_name(item.photo)
        # 배경을 지운 사진이 있으면 화면에는 그걸 보여 준다
        out.photo_url = f"/media/{cut}" if (config.MEDIA_DIR / cut).exists() else f"/media/{item.photo}"
    return out


async def save_cutout(filename: str, data: bytes) -> None:
    """배경 제거 사진을 만든다. rembg가 없거나 실패하면 조용히 건너뛴다 (원본 사진을 그대로 씀)."""
    if not config.PHOTO_CUTOUT:
        return
    try:
        from app.services import cutout
        result = await run_in_threadpool(cutout.make_cutout, data)
    except Exception as e:                   # 패키지 없음, 사진 손상 등 — 원본으로 충분하다
        log.warning("배경 제거 실패: %s", e)
        return
    if result:
        (config.MEDIA_DIR / cut_name(filename)).write_bytes(result)


def get_own_item(item_id: int, user: User, db: Session) -> ClothingItem:
    item = db.get(ClothingItem, item_id)
    if item is None or item.user_id != user.id:     # 남의 옷은 '없음'으로 답한다
        raise HTTPException(status.HTTP_404_NOT_FOUND, "옷을 찾을 수 없습니다.")
    return item


def delete_photo(filename: str | None):
    if filename:
        (config.MEDIA_DIR / filename).unlink(missing_ok=True)
        (config.MEDIA_DIR / cut_name(filename)).unlink(missing_ok=True)


@router.get("", response_model=list[ItemOut])
def list_items(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(ClothingItem).where(ClothingItem.user_id == user.id)
                       .order_by(ClothingItem.created_at.desc(), ClothingItem.id.desc()))
    return [to_out(it) for it in items]


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    category: Category = Form(...),
    season: Season = Form("사계절"),
    season_by_user: bool = Form(False),      # 화면에서 사용자가 계절을 직접 골랐는지
    style: Style = Form("캐주얼"),
    color: str = Form("", max_length=20),
    name: str = Form("", max_length=50),
    photo: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = len(db.scalars(select(ClothingItem.id).where(ClothingItem.user_id == user.id)).all())
    if count >= MAX_ITEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"옷은 {MAX_ITEMS}벌까지 등록할 수 있습니다.")

    filename = None
    if photo is not None and photo.filename:
        ext = ALLOWED_TYPES.get(photo.content_type or "")
        if ext is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "JPG, PNG, WEBP 사진만 올릴 수 있습니다.")
        data = await photo.read(config.MAX_PHOTO_BYTES + 1)
        if len(data) > config.MAX_PHOTO_BYTES:
            raise HTTPException(413, "사진은 2MB 이하만 올릴 수 있습니다.")
        config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        (config.MEDIA_DIR / filename).write_bytes(data)
        await save_cutout(filename, data)

    item = ClothingItem(user_id=user.id, category=category, season=season, season_by_user=season_by_user, style=style,
                        color=color.strip(), name=name.strip(), photo=filename)
    db.add(item)
    db.commit()
    return to_out(item)


@router.post("/analyze")
async def analyze_photo(photo: UploadFile = File(...), user: User = Depends(get_current_user)):
    """사진을 저장하지 않고 분석만 한다. 화면은 결과로 입력칸을 미리 채우고, 사용자가 확인한 뒤 저장한다."""
    if (photo.content_type or "") not in ALLOWED_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "JPG, PNG, WEBP 사진만 분석할 수 있습니다.")
    data = await photo.read(config.MAX_PHOTO_BYTES + 1)
    if len(data) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, "사진은 2MB 이하만 올릴 수 있습니다.")
    return await run_tagger(data)


async def run_tagger(data: bytes) -> dict:
    unavailable = HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "사진 자동 분석이 설치되지 않았어요. 직접 골라 주세요.")
    try:
        from app.services import tagger      # numpy·Pillow가 없으면 ImportError
    except ImportError:
        raise unavailable
    try:
        return await run_in_threadpool(tagger.analyze, data)   # 무거운 계산은 별도 스레드에서
    except tagger.TaggerUnavailable:          # torch·transformers가 없음
        raise unavailable
    except OSError:                           # 사진 파일이 깨졌거나 이미지가 아님
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "사진을 읽지 못했어요. 다른 사진으로 시도해 주세요.")


CONFIDENT_CATEGORY = 0.5


@router.post("/{item_id}/reanalyze", response_model=ItemOut)
async def reanalyze(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """이미 올린 옷을 지금의 분석 방식으로 다시 분류한다. 모델을 바꿔도 옷을 다시 올릴 필요가 없다.
    종류는 확신할 때(50% 이상)만 바꾸고, 스타일·색은 새 결과로 바꾼다. 계절은 사용자가 직접 고른 적이 없을 때만 바꾼다.
    이름은 비어 있을 때만 AI가 제안한 이름(예: "블랙 토트백")으로 채운다. 배경 제거 사진이 없으면 만든다."""
    item = get_own_item(item_id, user, db)
    if not item.photo or not (config.MEDIA_DIR / item.photo).exists():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "사진이 없는 옷은 다시 분석할 수 없어요.")
    data = (config.MEDIA_DIR / item.photo).read_bytes()
    tags = await run_tagger(data)
    if tags["category"]["confidence"] >= CONFIDENT_CATEGORY:
        item.category = tags["category"]["value"]
    if not item.season_by_user:
        item.season = tags["season"]["value"]
    item.style = tags["style"]["value"]
    item.color = tags["color"]["value"]
    if not item.name and tags.get("name"):
        item.name = tags["name"]["value"][:50]
    if not (config.MEDIA_DIR / cut_name(item.photo)).exists():
        await save_cutout(item.photo, data)
    db.commit()
    return to_out(item)


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: int, req: ItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = get_own_item(item_id, user, db)
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    if req.season is not None:
        item.season_by_user = True
    db.commit()
    return to_out(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = get_own_item(item_id, user, db)
    photo = item.photo
    db.delete(item)
    db.commit()
    delete_photo(photo)


@router.post("/wear", response_model=list[ItemOut])
def wear(req: WearRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """'이 코디로 결정'을 누르면 기록한다. 다음 추천에서 최근 입은 옷은 점수가 낮아진다."""
    items = [get_own_item(i, user, db) for i in dict.fromkeys(req.item_ids)]
    now = datetime.now(timezone.utc)
    for it in items:
        it.last_worn_at = now
    db.commit()
    return [to_out(it) for it in items]
