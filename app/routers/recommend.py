"""
AI 코디 추천

1. 내 옷으로 만들 수 있는 (상의, 하의, 아우터) 조합을 모두 만들고 조합 단위로 점수를 매긴다 (app/services/outfits.py)
   — 날씨 · 상황(FashionCLIP 사진 분석) · 색 조합 규칙 · 기분/스타일 · 최근 착용
2. 서로 너무 비슷하지 않은 상위 3개 코디를 고른다
3. LLM은 옷을 고르지 않고, 고른 코디의 설명만 쓴다. LLM이 실패해도 규칙이 만든 이유와 함께 코디는 보여 준다.
4. 사용자가 코디에 좋아요/별로를 남기면 저장해, 점수가 사람의 평가와 맞는지 잰다 (scripts/eval_outfits.py)

옷장이 비어 있으면 예전처럼 LLM에게 일반적인 코디 조언을 받는다.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ClothingItem, OutfitFeedback, User
from app.routers.wardrobe import get_own_item, to_out
from app.schemas import FeedbackRequest, OutfitOut, Pick, RecommendRequest, RecommendResponse, SampleResponse
from app.security import get_current_user
from app.services import llm, outfits as engine

router = APIRouter(prefix="/api", tags=["추천"])
log = logging.getLogger("moodfit")


def felt_temp(req) -> float:
    w = req.weather
    return w.feels_like if w.feels_like is not None else w.temp


def to_outfit_out(o: engine.Outfit, comment: str = "") -> OutfitOut:
    return OutfitOut(
        picks=[Pick(slot=s, item=to_out(it)) for s, it in o.slots.items()],
        score=round(o.score),
        breakdown={k: round(v * 100) for k, v in o.parts.items()},
        reasons=o.reasons,
        comment=comment,
    )


def my_items(user: User, db: Session):
    return list(db.scalars(select(ClothingItem).where(ClothingItem.user_id == user.id)))


@router.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = my_items(user, db)
    best = engine.recommend(items, temp=felt_temp(req), occasions=req.occasions, mood=req.mood, style=req.style)

    if not best:   # 상의·하의가 없으면 내 옷으로 코디를 만들 수 없다 → 일반 조언
        prompt = llm.build_prompt(req.weather, req.mood, req.occasions, req.style, user)
        try:
            text = llm.ask_groq(prompt).strip()
        except llm.LLMNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "서버에 AI API 키가 설정되지 않았어요.")
        except llm.LLMError as e:
            log.error("추천 실패: %s", e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "AI 추천을 받지 못했어요. 잠시 후 다시 시도해 주세요.")
        return RecommendResponse(text=text, outfits=[], picks=[], used_wardrobe=False, llm_used=True)

    comments, llm_used = [""] * len(best), False
    try:
        answer = llm.ask_groq(llm.build_explain_prompt(req.weather, req.mood, req.occasions, req.style, user, best))
        comments, llm_used = llm.parse_comments(answer, len(best)), True
    except (llm.LLMNotConfigured, llm.LLMError) as e:   # 설명이 없어도 코디와 규칙 이유는 보여 줄 수 있다
        log.warning("코디 설명 생략: %s", e)

    out = [to_outfit_out(o, c) for o, c in zip(best, comments)]
    log.info("추천 완료: 옷 %d벌 → 코디 %d개 (1위 %d점)", len(items), len(out), out[0].score)
    return RecommendResponse(text="", outfits=out, picks=out[0].picks, used_wardrobe=True, llm_used=llm_used)


@router.post("/recommend/sample", response_model=SampleResponse)
def sample(req: RecommendRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """평가용 코디. 점수 상위·중간·하위 구간에서 섞어 뽑는다 (점수는 화면에 보여 주지 않는다)."""
    items = [it for it in my_items(user, db) if it.category != "미분류"]
    o = engine.sample_for_rating(items, temp=felt_temp(req), occasions=req.occasions, mood=req.mood, style=req.style)
    return SampleResponse(outfit=to_outfit_out(o) if o else None)


@router.post("/recommend/feedback", status_code=status.HTTP_201_CREATED)
def feedback(req: FeedbackRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    slots = {slot: get_own_item(item_id, user, db) for slot, item_id in req.item_ids.items()}   # 남의 옷이면 404
    if "top" not in slots or "bottom" not in slots:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "상의와 하의가 있는 코디만 평가할 수 있어요.")
    temp = req.weather.feels_like if req.weather.feels_like is not None else req.weather.temp
    o = engine.score_outfit(slots, temp=temp, occasions=req.occasions, mood=req.mood, style=req.style,
                            now=datetime.now(timezone.utc))
    db.add(OutfitFeedback(user_id=user.id, item_ids=json.dumps(req.item_ids), temp=temp, occasions=",".join(req.occasions),
                          mood=req.mood, style=req.style, source=req.source, score=o.score, rating=req.rating))
    db.commit()
    count = db.query(OutfitFeedback).filter(OutfitFeedback.user_id == user.id).count()
    return {"saved": True, "count": count}
