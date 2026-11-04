"""
옷장 — 옷 등록(사진 업로드), 목록, 수정, 삭제, '오늘 이 코디 입음' 기록

사진은 브라우저에서 긴 변 800px JPEG로 줄여서 올리고, 서버는 media 폴더에 무작위 파일 이름으로 저장한다.
DB에는 파일 이름만 남긴다. (예전 버전은 사진을 브라우저 localStorage에 넣어 5MB 한도에 금방 찼다)
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import ClothingItem, User
from app.schemas import Category, ItemOut, ItemUpdate, Season, Style, WearRequest
from app.security import get_current_user

router = APIRouter(prefix="/api/wardrobe", tags=["옷장"])

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_ITEMS = 200


def to_out(item: ClothingItem) -> ItemOut:
    out = ItemOut.model_validate(item)
    out.photo_url = f"/media/{item.photo}" if item.photo else None
    return out


def get_own_item(item_id: int, user: User, db: Session) -> ClothingItem:
    item = db.get(ClothingItem, item_id)
    if item is None or item.user_id != user.id:     # 남의 옷은 '없음'으로 답한다
        raise HTTPException(status.HTTP_404_NOT_FOUND, "옷을 찾을 수 없습니다.")
    return item


def delete_photo(filename: str | None):
    if filename:
        (config.MEDIA_DIR / filename).unlink(missing_ok=True)


@router.get("", response_model=list[ItemOut])
def list_items(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(ClothingItem).where(ClothingItem.user_id == user.id)
                       .order_by(ClothingItem.created_at.desc(), ClothingItem.id.desc()))
    return [to_out(it) for it in items]


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    category: Category = Form(...),
    season: Season = Form("사계절"),
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

    item = ClothingItem(user_id=user.id, category=category, season=season, style=style,
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


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: int, req: ItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = get_own_item(item_id, user, db)
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
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
