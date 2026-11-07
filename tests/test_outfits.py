"""코디 조합 점수 — DB 없이 순수 함수만 확인한다."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import outfits as engine

NOW = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)


def item(id, category, color="블랙", season="사계절", style="캐주얼", fit=None, worn=None, name=""):
    return SimpleNamespace(id=id, category=category, color=color, season=season, style=style, name=name,
                           occasion_fit=json.dumps(fit) if fit else None, last_worn_at=worn)


def best(items, temp=15, occasions=(), mood="편안함", style="캐주얼", k=3):
    return engine.recommend(items, temp=temp, occasions=list(occasions), mood=mood, style=style, now=NOW, k=k)


def test_color_rules():
    t = lambda c: item(1, "상의", color=c)
    b = lambda c: item(2, "하의", color=c)
    assert engine.color_harmony({"top": t("핑크"), "bottom": b("블랙")})[0] == 1.0          # 뉴트럴 + 포인트 1색
    assert engine.color_harmony({"top": t("핑크"), "bottom": b("그린")})[0] < 0.5           # 포인트 2색
    assert engine.color_harmony({"top": t("핑크"), "bottom": b("블루")})[0] == 1.0          # 청바지는 무채색 취급
    score, why = engine.color_harmony({"top": t("베이지"), "bottom": b("브라운")})
    assert "톤온톤" in why
    assert engine.color_harmony({"top": t("블랙"), "bottom": b("네이비")})[0] < engine.color_harmony({"top": t("블랙"), "bottom": b("그레이")})[0]


def test_outer_required_when_cold_and_dropped_when_hot():
    items = [item(1, "상의"), item(2, "하의"), item(3, "아우터", season="겨울")]
    assert "outer" in best(items, temp=5)[0].slots
    assert all("outer" not in o.slots for o in best(items, temp=27))


def test_winter_clothes_lose_in_summer():
    items = [item(1, "상의", season="겨울", name="니트"), item(2, "상의", season="여름", name="반팔"), item(3, "하의")]
    top = best(items, temp=28)[0]
    assert top.slots["top"].name == "반팔"


def test_clip_occasion_fit_beats_rule():
    office = {"출근": 0.6, "운동": 0.05, "집에서": 0.1}
    gym = {"출근": 0.05, "운동": 0.7, "집에서": 0.1}
    items = [item(1, "상의", fit=office, name="셔츠"), item(2, "상의", fit=gym, name="트레이닝 상의"), item(3, "하의")]
    assert best(items, occasions=["출근"])[0].slots["top"].name == "셔츠"
    assert best(items, occasions=["운동"])[0].slots["top"].name == "트레이닝 상의"
    assert "사진 분석" in " ".join(best(items, occasions=["출근"])[0].reasons)


def test_recently_worn_is_less_likely_and_results_are_diverse():
    items = [item(1, "상의", worn=NOW - timedelta(days=1)), item(2, "상의"), item(3, "하의"), item(4, "하의", color="그레이")]
    picks = best(items)
    assert picks[0].slots["top"].id == 2
    keys = [(o.slots["top"].id, o.slots["bottom"].id) for o in picks]
    assert len(set(keys)) == len(keys)


def test_needs_top_and_bottom():
    assert best([item(1, "상의"), item(2, "아우터")]) == []
    assert engine.random_outfit([item(1, "상의")], 15) is None


def test_rating_sample_mixes_score_bands():
    import random
    items = [item(i, "상의", color=c, season=s) for i, (c, s) in enumerate(
        [("화이트", "가을"), ("핑크", "여름"), ("그린", "겨울"), ("블랙", "가을"), ("퍼플", "여름")])]
    items += [item(10 + i, "하의", color=c, season=s) for i, (c, s) in enumerate(
        [("블랙", "가을"), ("오렌지", "여름"), ("블루", "사계절"), ("레드", "겨울")])]
    rng = random.Random(0)
    scores = [engine.sample_for_rating(items, temp=15, occasions=[], mood="편안함", style="캐주얼", now=NOW, rng=rng).score
              for _ in range(60)]
    best_score = best(items, k=1)[0].score
    high = sum(s >= best_score - 5 for s in scores)
    assert 10 < high < 55 and min(scores) < best_score - 15     # 좋은 코디가 많지만, 별로인 코디도 섞인다


def test_shoes_follow_weather():
    items = [item(1, "상의"), item(2, "하의"), item(3, "신발", season="겨울", name="부츠"), item(4, "신발", season="여름", name="샌들")]
    assert best(items, temp=28)[0].slots["shoes"].name == "샌들"
    assert best(items, temp=2)[0].slots["shoes"].name == "부츠"
