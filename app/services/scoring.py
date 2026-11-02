"""
scoring.py — 옷장 아이템에 오늘 상황 점수를 매긴다 (규칙 기반)

LLM에게 옷장 전체를 그냥 넘기면 계절에 안 맞는 옷을 고르거나, 매번 같은 옷만 고르는 일이 생긴다.
그래서 먼저 규칙으로 점수를 매겨 카테고리별 1순위(★)와 계절이 안 맞는 옷(⚠)을 표시해 넘기고,
LLM은 그 안에서 조합과 설명을 맡는다.

점수 (최대 90점)
  날씨(계절) 적합도  30  기온으로 정한 오늘 계절과 옷의 계절이 맞는지
  상황 적합도        25  출근·데이트 같은 상황에 어울리는 스타일인지
  기분 적합도        20  오늘 기분에 어울리는 스타일인지
  최근 착용 역가중치 10  최근 3일 안에 입은 옷은 점수를 낮춰 같은 옷 반복을 줄임
  색상 정보           5  색상이 입력된 옷은 LLM이 색 조합을 판단할 수 있어 약간 우대
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

OCCASION_STYLE = {
    "출근": ["포멀", "미니멀"], "데이트": ["캐주얼", "미니멀"], "친구 약속": ["캐주얼", "스트릿"],
    "면접": ["포멀"], "외출": ["캐주얼"], "운동": ["스포티"], "여행": ["캐주얼", "스트릿"], "집에서": ["캐주얼"],
}
MOOD_STYLE = {
    "신남": ["스트릿", "캐주얼"], "편안함": ["캐주얼"], "진지함": ["포멀", "미니멀"],
    "설렘": ["캐주얼", "미니멀"], "피곤함": ["캐주얼"], "자신감": ["포멀", "스트릿"],
}

RECENT_DAYS = 3
GOOD_SEASON_FIT = 12   # 계절 점수가 이보다 낮으면 1순위(★)로 추천하지 않는다
BAD_SEASON_FIT = 2     # 이 점수 이하면 '계절이 안 맞음(⚠)'으로 표시


def season_bucket(temp_c: float) -> str:
    """기온으로 오늘의 계절을 정한다. 23도 이상 여름, 8도 이하 겨울, 그 사이는 간절기."""
    if temp_c >= 23:
        return "여름"
    if temp_c <= 8:
        return "겨울"
    return "간절기"


def season_score(item_season: str, bucket: str) -> int:
    if item_season == "사계절":
        return 24
    if bucket == "간절기":
        return 30 if item_season in ("봄", "가을") else 12
    if item_season == bucket:
        return 30
    if item_season in ("봄", "가을"):
        return 12
    return BAD_SEASON_FIT          # 예: 한여름에 겨울 아우터


@dataclass
class Scored:
    item: object          # ClothingItem
    score: int
    season_fit: int
    reason: str


def score_item(item, bucket: str, occasions: list[str], mood: str, preferred_style: str,
               now: datetime | None = None) -> Scored:
    now = now or datetime.now(timezone.utc)
    occ_styles = {s for o in occasions for s in OCCASION_STYLE.get(o, [])}
    mood_styles = set(MOOD_STYLE.get(mood, []))
    reasons = []

    fit = season_score(item.season, bucket)
    score = fit
    if fit >= 30:
        reasons.append("계절")

    if item.style in occ_styles:
        score += 25
        reasons.append("상황")
    elif item.style == preferred_style:
        score += 15
        reasons.append("선호 스타일")
    else:
        score += 5

    if item.style in mood_styles:
        score += 20
        reasons.append("기분")
    else:
        score += 6

    worn = item.last_worn_at
    if worn is not None and worn.tzinfo is None:          # SQLite는 시간대 정보를 저장하지 않는다
        worn = worn.replace(tzinfo=timezone.utc)
    if worn is not None and now - worn < timedelta(days=RECENT_DAYS):
        score += 2
        reasons.append("최근 착용(감점)")
    else:
        score += 10

    score += 5 if item.color else 0
    return Scored(item, score, fit, ", ".join(reasons) or "기본 점수")


def rank_wardrobe(items, temp_c: float, occasions: list[str], mood: str, preferred_style: str,
                  now: datetime | None = None):
    """점수 순으로 정렬한 목록과, 카테고리별 1순위 아이템 id를 돌려준다. 미분류 옷은 제외."""
    bucket = season_bucket(temp_c)
    usable = [it for it in items if it.category != "미분류"]
    ranked = sorted(
        (score_item(it, bucket, occasions, mood, preferred_style, now) for it in usable),
        key=lambda s: (-s.score, s.item.id),
    )
    top_by_category: dict[str, int] = {}
    for s in ranked:
        if s.season_fit >= GOOD_SEASON_FIT and s.item.category not in top_by_category:
            top_by_category[s.item.category] = s.item.id
    return ranked, top_by_category


def wardrobe_prompt_lines(ranked: list[Scored], top_by_category: dict[str, int]) -> str:
    lines = []
    for s in ranked:
        it = s.item
        tag = ""
        if top_by_category.get(it.category) == it.id:
            tag = " ★오늘 날씨·상황·기분에 잘 맞음"
        elif s.season_fit <= BAD_SEASON_FIT:
            tag = " ⚠오늘 기온과 계절이 안 맞을 수 있음"
        name = f" · {it.name}" if it.name else ""
        lines.append(f"- [{it.id}] {it.category} · {it.color or '색상 미상'} · {it.season} · {it.style}{name}{tag}")
    return "\n".join(lines)
