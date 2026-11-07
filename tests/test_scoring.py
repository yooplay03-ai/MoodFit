"""기본 규칙 — 기온 → 계절 구간, 계절 점수"""

from app.services.scoring import season_bucket, season_score


def test_season_bucket_boundaries():
    assert season_bucket(23) == "여름" and season_bucket(22.9) == "간절기"
    assert season_bucket(8) == "겨울" and season_bucket(8.1) == "간절기"


def test_season_score():
    assert season_score("겨울", "여름") < season_score("가을", "여름") < season_score("여름", "여름")
    assert season_score("봄", "간절기") == season_score("가을", "간절기") == 30
