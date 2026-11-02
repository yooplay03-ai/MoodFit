"""
AI 코디 추천

1. 내 옷장을 규칙 점수로 정렬하고 카테고리별 1순위(★)와 계절이 안 맞는 옷(⚠)을 표시
2. 날씨·프로필·기분·상황과 함께 LLM에 전달
3. LLM이 고른 옷 id가 실제 내 옷이고 슬롯(상의·하의…)과 카테고리가 맞는지 서버에서 다시 확인
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ClothingItem, User
from app.routers.wardrobe import to_out
from app.schemas import Candidate, Pick, RecommendRequest, RecommendResponse
from app.security import get_current_user
from app.services import llm, scoring

router = APIRouter(prefix="/api", tags=["추천"])
log = logging.getLogger("moodfit")


def slot_matches(slot: str, category: str) -> bool:
    expected = llm.SLOT_CATEGORY[slot]
    return category in expected if isinstance(expected, tuple) else category == expected


@router.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = list(db.scalars(select(ClothingItem).where(ClothingItem.user_id == user.id)))
    ranked, top = scoring.rank_wardrobe(items, req.weather.temp, req.occasions, req.mood, req.style)
    wardrobe_text = scoring.wardrobe_prompt_lines(ranked, top) if ranked else None

    prompt = llm.build_prompt(req.weather, req.mood, req.occasions, req.style, user, wardrobe_text)
    try:
        answer = llm.ask_groq(prompt)
    except llm.LLMNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "서버에 AI API 키가 설정되지 않았어요.")
    except llm.LLMError as e:
        log.error("추천 실패: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "AI 추천을 받지 못했어요. 잠시 후 다시 시도해 주세요.")

    picks: list[Pick] = []
    text = answer.strip()
    if wardrobe_text is not None:
        chosen, text = llm.parse_picks(answer)
        own = {it.id: it for it in items}
        for slot in llm.SLOTS:
            item = own.get(chosen.get(slot))
            if item is not None and slot_matches(slot, item.category):
                picks.append(Pick(slot=slot, item=to_out(item)))
            elif slot in chosen:
                log.warning("LLM이 고른 옷을 버림: slot=%s id=%s", slot, chosen[slot])

    by_id = {s.item.id: s for s in ranked}
    candidates = [Candidate(item_id=i, score=by_id[i].score, reason=by_id[i].reason) for i in top.values()]
    log.info("추천 완료: 옷 %d벌 중 %d개 선택", len(items), len(picks))
    return RecommendResponse(text=text, picks=picks, candidates=candidates, used_wardrobe=wardrobe_text is not None)
