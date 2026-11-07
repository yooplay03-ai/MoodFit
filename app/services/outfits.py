"""
outfits.py — 내 옷으로 만들 수 있는 코디 조합을 모두 만들고, 조합 단위로 점수를 매겨 상위 코디를 고른다.

예전 방식은 옷을 한 벌씩 따로 점수 매기고 조합은 LLM이 골랐다. 그러면 색이 안 어울리는 조합이나
상황에 안 맞는 조합을 막을 수 없고, 왜 그 조합인지 설명할 수 없었다. 지금은 조합마다 아래 점수를 계산한다.

  날씨   35  옷마다 계절이 오늘 기온(체감)에 맞는지. 추우면 아우터를 꼭 넣고, 더우면 빼고 계산한다.
  상황   30  FashionCLIP이 사진을 보고 계산한 "이 옷이 출근·데이트…에 어울리는 정도" (사진 없는 옷은 스타일 규칙)
  색     20  무채색 + 포인트 1색, 톤온톤처럼 설명할 수 있는 색 조합 규칙
  스타일  5  오늘 기분·선호 스타일과 맞는지
  새로움 10  최근 3일 안에 입은 옷이 적을수록 높다

점수 계산은 LLM 없이 규칙과 미리 계산한 값으로만 하므로, 같은 조건이면 늘 같은 결과가 나오고 이유를 그대로 보여 줄 수 있다.
LLM은 고른 코디의 설명만 쓴다.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import product

from app.services.scoring import MOOD_STYLE, OCCASION_STYLE, RECENT_DAYS, season_bucket, season_score

WEIGHTS = {"weather": 35, "occasion": 30, "color": 20, "style": 5, "fresh": 10}
COMPONENT_LABEL = {"weather": "날씨", "occasion": "상황", "color": "색 조합", "style": "기분·스타일", "fresh": "새로움"}

OUTER_REQUIRED_BELOW = 17      # 체감 17도 미만이면 아우터를 꼭 넣는다
OUTER_ALLOWED_BELOW = 23       # 17~22도는 넣어도 되고 안 넣어도 된다. 23도 이상은 넣지 않는다

# 색 규칙
NEUTRALS = {"화이트", "아이보리", "베이지", "그레이", "차콜", "블랙", "네이비", "브라운", "카키"}
MONO = {"화이트", "그레이", "차콜", "블랙"}
DENIM = {"블루", "스카이블루"}                 # 청바지 색은 하의에서는 무채색처럼 어디에나 어울린다
FAMILIES = {
    "따뜻한 톤": {"아이보리", "베이지", "브라운", "카키", "오렌지", "옐로우", "와인"},
    "차가운 톤": {"네이비", "블루", "스카이블루", "그레이", "차콜", "라벤더", "민트", "퍼플"},
    "무채색": MONO,
}


@dataclass
class Outfit:
    slots: dict                                   # slot → ClothingItem (top, bottom, outer, accessory)
    parts: dict = field(default_factory=dict)     # 항목별 0~1 점수
    reasons: list = field(default_factory=list)
    score: float = 0.0

    def key(self):
        return tuple(sorted((s, it.id) for s, it in self.slots.items()))


def _worn_recently(item, now) -> bool:
    worn = item.last_worn_at
    if worn is None:
        return False
    if worn.tzinfo is None:                       # SQLite는 시간대 정보를 저장하지 않는다
        worn = worn.replace(tzinfo=timezone.utc)
    return now - worn < timedelta(days=RECENT_DAYS)


def occasion_fit(item, occasions: list[str]) -> tuple[float, str]:
    """옷 하나가 오늘 상황에 어울리는 정도(0~1)와 계산 방법."""
    if not occasions:
        return 0.7, "none"
    fit = getattr(item, "occasion_fit", None)
    scores = json.loads(fit) if isinstance(fit, str) and fit else (fit or {})
    if scores:
        # CLIP 확률을 그 옷이 가장 어울리는 상황 대비 비율로 바꾼다 (옷마다 확률 분포의 퍼짐이 달라서)
        best = max(scores.values()) or 1.0
        return sum(min(1.0, scores.get(o, 0.0) / best) for o in occasions) / len(occasions), "clip"
    styles = {s for o in occasions for s in OCCASION_STYLE.get(o, [])}
    return (1.0 if item.style in styles else 0.35), "rule"


def color_harmony(slots: dict) -> tuple[float, str]:
    """코디 전체의 색 조합 점수(0~1)와 이유."""
    colors = []
    for slot, it in slots.items():
        c = it.color or ""
        if not c:
            continue
        colors.append("네이비" if slot == "bottom" and c in DENIM else c)   # 청바지 → 무채색 취급
    if not colors:
        return 0.6, "색 정보가 없어 색 조합은 판단하지 않았어요"
    distinct = list(dict.fromkeys(colors))
    accents = [c for c in distinct if c not in NEUTRALS]
    if len(accents) == 0:
        if set(distinct) <= MONO:
            score, why = 0.85, "무채색으로만 맞춘 깔끔한 조합"
        else:
            score, why = 0.9, "뉴트럴 톤으로 차분하게 맞춘 조합"
    elif len(accents) == 1:
        score, why = 1.0, f"뉴트럴 + 포인트 1색({accents[0]})이라 안정적"
    elif len(accents) == 2 and any(set(accents) <= fam for fam in FAMILIES.values()):
        score, why = 0.7, f"포인트 색 2개({', '.join(accents)})지만 같은 계열이라 무난"
    elif len(accents) == 2:
        score, why = 0.35, f"포인트 색이 2개({', '.join(accents)})라 산만해 보일 수 있음"
    else:
        score, why = 0.15, f"색이 {len(accents)}가지나 섞여 복잡함"
    if len(distinct) >= 2 and any(set(distinct) <= fam for name, fam in FAMILIES.items() if name != "무채색"):
        score, why = min(1.0, score + 0.1), why + " · 톤온톤"
    top, bottom = slots.get("top"), slots.get("bottom")
    if top and bottom and {top.color, bottom.color} == {"블랙", "네이비"}:
        score, why = max(0.0, score - 0.2), why + " · 블랙과 네이비는 비슷해 보여 애매할 수 있음"
    return score, why


def score_outfit(slots: dict, *, temp: float, occasions: list[str], mood: str, style: str, now: datetime,
                 weights: dict | None = None) -> Outfit:
    weights = weights or WEIGHTS
    items = list(slots.values())
    bucket = season_bucket(temp)
    o = Outfit(slots=slots)

    season_fits = [season_score(it.season, bucket) / 30 for it in items]
    o.parts["weather"] = sum(season_fits) / len(season_fits)
    bad = [it for it, f in zip(items, season_fits) if f < 0.2]
    if bad:
        o.reasons.append(f"⚠ 오늘 기온과 계절이 안 맞는 옷이 있어요: {', '.join(i.name or i.category for i in bad)}")
    elif "outer" in slots:
        o.reasons.append(f"체감 {temp:.0f}°C라 아우터까지 챙겼어요")
    else:
        o.reasons.append(f"체감 {temp:.0f}°C에 맞는 두께예요")

    fits = [occasion_fit(it, occasions) for it in items]
    o.parts["occasion"] = sum(f for f, _ in fits) / len(fits)
    if occasions:
        how = "사진 분석" if any(m == "clip" for _, m in fits) else "스타일 규칙"
        level = "잘 어울려요" if o.parts["occasion"] >= 0.7 else "무난해요" if o.parts["occasion"] >= 0.45 else "조금 덜 어울려요"
        o.reasons.append(f"{'·'.join(occasions)}에 {level} ({how})")

    o.parts["color"], why = color_harmony(slots)
    o.reasons.append(why)

    liked = set(MOOD_STYLE.get(mood, [])) | {style}
    o.parts["style"] = sum(1.0 if it.style in liked else 0.5 for it in items) / len(items)

    recent = [it for it in items if _worn_recently(it, now)]
    o.parts["fresh"] = 1 - len(recent) / len(items)
    if recent:
        o.reasons.append(f"최근에 입은 옷이 있어요: {', '.join(i.name or i.category for i in recent)}")

    o.score = sum(weights[k] * o.parts[k] for k in weights)
    return o


def best_extra(slots: dict, slot: str, options: list, temp: float):
    """신발·가방은 조합마다 하나씩 붙인다: 오늘 기온에 맞고(겨울 부츠·비니를 여름에 X) 색이 잘 맞는 것"""
    bucket = season_bucket(temp)
    return max(options, key=lambda e: (season_score(e.season, bucket) / 30 + color_harmony({**slots, slot: e})[0], -e.id))


def candidate_slots(items, temp: float):
    """가능한 모든 (상의, 하의, 아우터) 조합. 신발과 가방·액세서리는 조합마다 가장 잘 맞는 것 하나를 붙인다."""
    by = {}
    for it in items:
        by.setdefault(it.category, []).append(it)
    tops, bottoms = by.get("상의", []), by.get("하의", [])
    outers = by.get("아우터", [])
    if temp < OUTER_REQUIRED_BELOW and outers:
        outer_options = outers
    elif temp < OUTER_ALLOWED_BELOW:
        outer_options = [None, *outers]
    else:
        outer_options = [None]
    extras = by.get("가방", []) + by.get("액세서리", [])
    shoes = by.get("신발", [])
    for top, bottom, outer in product(tops, bottoms, outer_options):
        slots = {"top": top, "bottom": bottom}
        if outer is not None:
            slots["outer"] = outer
        if shoes:
            slots["shoes"] = best_extra(slots, "shoes", shoes, temp)
        if extras:
            slots["accessory"] = best_extra(slots, "accessory", extras, temp)
        yield slots


PER_CATEGORY = 12     # 옷이 많아도 조합 수가 폭발하지 않게, 종류별로 개별 점수 상위 12벌만 조합에 쓴다


def prefilter(items, temp: float, occasions: list[str]):
    bucket = season_bucket(temp)
    by = {}
    for it in items:
        by.setdefault(it.category, []).append(it)
    kept = []
    for group in by.values():
        group.sort(key=lambda it: (-(season_score(it.season, bucket) / 30 + occasion_fit(it, occasions)[0]), it.id))
        kept += group[:PER_CATEGORY]
    return kept


def recommend(items, *, temp: float, occasions: list[str], mood: str, style: str,
              now: datetime | None = None, k: int = 3, weights: dict | None = None) -> list[Outfit]:
    """점수 순 상위 k개. 서로 너무 비슷한 코디(상의·하의·아우터 중 2개 이상 같음)는 건너뛴다."""
    now = now or datetime.now(timezone.utc)
    usable = prefilter([it for it in items if it.category != "미분류"], temp, occasions)
    scored = [score_outfit(s, temp=temp, occasions=occasions, mood=mood, style=style, now=now, weights=weights)
              for s in candidate_slots(usable, temp)]
    scored.sort(key=lambda o: (-o.score, o.key()))
    chosen: list[Outfit] = []
    for o in scored:
        core = {s: o.slots.get(s) for s in ("top", "bottom", "outer")}
        if all(sum(core[s] is not None and core[s] is c.slots.get(s) for s in core) < 2 for c in chosen):
            chosen.append(o)
        if len(chosen) == k:
            break
    return chosen


SAMPLE_POOL = 30
SAMPLE_BANDS = ((0.5, 0.0, 1 / 3), (0.3, 1 / 3, 2 / 3), (0.2, 2 / 3, 1.0))   # (확률, 점수 순위 구간) 상위·중간·하위


def sample_for_rating(items, *, temp: float, occasions: list[str], mood: str, style: str,
                      now: datetime | None = None, rng: random.Random | None = None):
    """평가용 코디. 무작위 코디 30개를 점수 순으로 줄 세운 뒤 상위 1/3에서 50%, 중간 30%, 하위 20% 확률로 하나를 고른다.
    완전 무작위면 대부분 엉뚱한 코디라 평가하는 사람이 지치고, 추천 결과만 평가하면 높은 점수만 모여 비교가 안 된다.
    점수 구간을 섞어 뽑으면 좋은 코디와 별로인 코디가 모두 들어와 '점수가 취향을 가르는지'를 잴 수 있다."""
    rng = rng or random.Random()
    now = now or datetime.now(timezone.utc)
    pool, seen = [], set()
    for _ in range(SAMPLE_POOL * 3):
        slots = random_outfit(items, temp, rng)
        if slots is None:
            return None
        o = score_outfit(slots, temp=temp, occasions=occasions, mood=mood, style=style, now=now)
        if o.key() not in seen:
            seen.add(o.key())
            pool.append(o)
        if len(pool) == SAMPLE_POOL:
            break
    pool.sort(key=lambda x: -x.score)
    r, acc = rng.random(), 0.0
    for p, lo, hi in SAMPLE_BANDS:
        acc += p
        if r <= acc:
            band = pool[int(lo * len(pool)): max(int(lo * len(pool)) + 1, int(hi * len(pool)))]
            return rng.choice(band)
    return pool[0]


def random_outfit(items, temp: float, rng: random.Random | None = None):
    """점수와 상관없이 무작위로 고른 코디 (평가용 후보를 만들 때 쓴다)"""
    rng = rng or random.Random()
    by = {}
    for it in items:
        by.setdefault(it.category, []).append(it)
    if not by.get("상의") or not by.get("하의"):
        return None
    slots = {"top": rng.choice(by["상의"]), "bottom": rng.choice(by["하의"])}
    outers = by.get("아우터", [])
    if outers and (temp < OUTER_REQUIRED_BELOW or (temp < OUTER_ALLOWED_BELOW and rng.random() < 0.5)):
        slots["outer"] = rng.choice(outers)
    if by.get("신발"):
        slots["shoes"] = rng.choice(by["신발"])
    extras = by.get("가방", []) + by.get("액세서리", [])
    if extras:
        slots["accessory"] = rng.choice(extras)
    return slots
