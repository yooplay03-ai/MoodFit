"""규칙 점수 — DB 없이 순수 함수만 확인한다."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.scoring import rank_wardrobe, season_bucket, wardrobe_prompt_lines

NOW = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)


def item(id, category, season="사계절", style="캐주얼", color="블랙", worn=None):
    return SimpleNamespace(id=id, category=category, season=season, style=style, color=color, name="", last_worn_at=worn)


def test_season_bucket_boundaries():
    assert season_bucket(23) == "여름" and season_bucket(22.9) == "간절기"
    assert season_bucket(8) == "겨울" and season_bucket(8.1) == "간절기"


def test_winter_coat_is_not_top_pick_in_summer():
    items = [item(1, "아우터", season="겨울"), item(2, "아우터", season="여름")]
    ranked, top = rank_wardrobe(items, 28, ["외출"], "편안함", "캐주얼", NOW)
    assert top["아우터"] == 2
    text = wardrobe_prompt_lines(ranked, top)
    assert "[1]" in text and "⚠" in text.split("[1]")[1].splitlines()[0]


def test_no_star_when_only_option_has_wrong_season():
    ranked, top = rank_wardrobe([item(1, "아우터", season="겨울")], 30, [], "편안함", "캐주얼", NOW)
    assert "아우터" not in top


def test_occasion_and_mood_raise_matching_style():
    items = [item(1, "상의", style="캐주얼"), item(2, "상의", style="포멀")]
    _, top = rank_wardrobe(items, 15, ["면접"], "진지함", "캐주얼", NOW)
    assert top["상의"] == 2


def test_recently_worn_item_is_lowered():
    items = [item(1, "하의", worn=NOW - timedelta(days=1)), item(2, "하의")]
    ranked, top = rank_wardrobe(items, 15, [], "편안함", "캐주얼", NOW)
    assert top["하의"] == 2
    assert ranked[0].score - ranked[1].score == 8


def test_unclassified_items_are_excluded():
    ranked, top = rank_wardrobe([item(1, "미분류")], 15, [], "편안함", "캐주얼", NOW)
    assert ranked == [] and top == {}
