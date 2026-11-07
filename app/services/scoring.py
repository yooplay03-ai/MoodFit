"""
scoring.py — 코디 점수에 쓰는 기본 규칙 (기온 → 계절, 상황·기분 → 어울리는 스타일)

조합 단위 점수 계산은 app/services/outfits.py에서 한다.
(예전에는 옷을 한 벌씩 점수 매겨 LLM에 넘기고 조합은 LLM이 골랐다. README '추천은 이렇게 동작합니다' 참고)
"""

OCCASION_STYLE = {
    "출근": ["포멀", "미니멀"], "데이트": ["캐주얼", "미니멀"], "친구 약속": ["캐주얼", "스트릿"],
    "면접": ["포멀"], "외출": ["캐주얼"], "운동": ["스포티"], "여행": ["캐주얼", "스트릿"], "집에서": ["캐주얼"],
}
MOOD_STYLE = {
    "신남": ["스트릿", "캐주얼"], "편안함": ["캐주얼"], "진지함": ["포멀", "미니멀"],
    "설렘": ["캐주얼", "미니멀"], "피곤함": ["캐주얼"], "자신감": ["포멀", "스트릿"],
}

RECENT_DAYS = 3
BAD_SEASON_FIT = 2     # 계절이 크게 안 맞음 (예: 한여름에 겨울 아우터)


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
