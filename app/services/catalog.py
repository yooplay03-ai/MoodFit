"""
catalog.py — '기본 옷으로 빠르게 채우기'에 쓰는 기본 아이템 목록.

옷을 하나씩 찍어 올리는 게 가장 큰 진입 장벽이라, 흔히 가진 기본 아이템을 눌러서 옷장을 먼저 채우게 한다.
사진은 무료 사진 API(Pixabay 또는 Pexels — 프로그램 사용을 공식 허용)로 모았다 — scripts/build_catalog.py
  - 아이템 정의(이름·종류·색·계절·스타일·검색어)는 이 파일
  - 고른 사진 정보(사진 id, 작가, 주소, 출처)와 FashionCLIP 상황 점수는 app/data/catalog.json (스크립트가 만듦)
  - 사진 파일은 media/catalog/ (Git에 올리지 않음).
    Pexels는 파일이 없으면 원래 주소에서 받아 쓰지만, Pixabay는 원래 주소를 계속 쓰는 것(핫링크)을 금지하고
    주소도 24시간 뒤 만료되므로 내려받은 파일이 있는 아이템만 보여 준다.
"""

import json
from functools import lru_cache

from app import config
from app.services.fetch import fetch

CATALOG_JSON = config.BASE_DIR / "app" / "data" / "catalog.json"
REMOTE_OK = {"Pexels"}                       # 원래 주소에서 직접 받아도 되는 출처
IMAGE_HOSTS = {"images.pexels.com"}

# (id, 이름, 종류, 색, 계절, 스타일, Pexels 검색어)
ITEMS = [
    ("white-tee", "화이트 반팔 티셔츠", "상의", "화이트", "여름", "캐주얼", "white t-shirt flat lay"),
    ("black-tee", "블랙 반팔 티셔츠", "상의", "블랙", "여름", "캐주얼", "black t-shirt flat lay"),
    ("white-shirt", "화이트 셔츠", "상의", "화이트", "봄", "미니멀", "white button up shirt"),
    ("blue-oxford", "스카이블루 옥스퍼드 셔츠", "상의", "스카이블루", "봄", "캐주얼", "light blue oxford shirt"),
    ("linen-shirt", "베이지 린넨 셔츠", "상의", "베이지", "여름", "미니멀", "beige linen shirt"),
    ("stripe-long-tee", "스트라이프 긴팔 티셔츠", "상의", "네이비", "봄", "캐주얼", "striped long sleeve shirt"),
    ("navy-polo", "네이비 폴로 셔츠", "상의", "네이비", "여름", "캐주얼", "navy polo shirt"),
    ("gray-sweatshirt", "그레이 맨투맨", "상의", "그레이", "가을", "캐주얼", "gray crewneck sweatshirt"),
    ("gray-hoodie", "그레이 후드티", "상의", "그레이", "가을", "스포티", "gray hoodie"),
    ("navy-knit", "네이비 니트", "상의", "네이비", "겨울", "미니멀", "navy knit sweater"),
    ("beige-knit", "베이지 니트", "상의", "베이지", "겨울", "캐주얼", "beige knit sweater"),
    ("black-turtleneck", "블랙 터틀넥", "상의", "블랙", "겨울", "미니멀", "black turtleneck sweater"),
    ("white-blouse", "화이트 블라우스", "상의", "화이트", "봄", "포멀", "white blouse"),
    ("blue-jeans", "블루 청바지", "하의", "블루", "사계절", "캐주얼", "blue jeans"),
    ("black-jeans", "블랙 청바지", "하의", "블랙", "사계절", "캐주얼", "black jeans"),
    ("black-slacks", "블랙 슬랙스", "하의", "블랙", "사계절", "포멀", "black dress pants"),
    ("beige-chinos", "베이지 치노 팬츠", "하의", "베이지", "봄", "캐주얼", "beige chino pants"),
    ("charcoal-wide", "차콜 와이드 팬츠", "하의", "차콜", "가을", "미니멀", "wide leg trousers"),
    ("gray-sweatpants", "그레이 트레이닝 바지", "하의", "그레이", "가을", "스포티", "gray sweatpants"),
    ("denim-shorts", "데님 반바지", "하의", "블루", "여름", "캐주얼", "denim shorts"),
    ("black-pleated-skirt", "블랙 플리츠 스커트", "하의", "블랙", "가을", "미니멀", "black pleated skirt"),
    ("beige-long-skirt", "베이지 롱스커트", "하의", "베이지", "봄", "캐주얼", "beige midi skirt"),
    ("beige-trench", "베이지 트렌치코트", "아우터", "베이지", "봄", "미니멀", "beige trench coat"),
    ("black-coat", "블랙 울 코트", "아우터", "블랙", "겨울", "포멀", "black wool coat"),
    ("camel-coat", "카멜 코트", "아우터", "브라운", "겨울", "미니멀", "camel coat"),
    ("denim-jacket", "데님 재킷", "아우터", "블루", "봄", "캐주얼", "denim jacket"),
    ("black-padding", "블랙 패딩", "아우터", "블랙", "겨울", "캐주얼", "black puffer jacket"),
    ("leather-jacket", "블랙 가죽 재킷", "아우터", "블랙", "가을", "스트릿", "black leather jacket"),
    ("gray-blazer", "그레이 블레이저", "아우터", "그레이", "가을", "포멀", "gray blazer"),
    ("navy-cardigan", "네이비 카디건", "아우터", "네이비", "봄", "캐주얼", "navy cardigan"),
    ("fleece", "아이보리 플리스 재킷", "아우터", "아이보리", "겨울", "캐주얼", "fleece jacket"),
    ("windbreaker", "블랙 바람막이", "아우터", "블랙", "봄", "스포티", "black windbreaker jacket"),
    ("white-sneakers", "화이트 스니커즈", "신발", "화이트", "사계절", "캐주얼", "white sneakers"),
    ("black-loafers", "블랙 로퍼", "신발", "블랙", "사계절", "포멀", "black loafers"),
    ("running-shoes", "그레이 러닝화", "신발", "그레이", "사계절", "스포티", "running shoes"),
    ("chelsea-boots", "블랙 첼시 부츠", "신발", "블랙", "겨울", "스트릿", "black chelsea boots"),
    ("sandals", "브라운 샌들", "신발", "브라운", "여름", "캐주얼", "leather sandals"),
    ("black-tote", "블랙 토트백", "가방", "블랙", "사계절", "미니멀", "black leather tote bag"),
    ("canvas-tote", "아이보리 에코백", "가방", "아이보리", "사계절", "캐주얼", "canvas tote bag"),
    ("brown-crossbody", "브라운 크로스백", "가방", "브라운", "사계절", "캐주얼", "brown crossbody bag"),
    ("black-backpack", "블랙 백팩", "가방", "블랙", "사계절", "스포티", "black backpack"),
    ("gray-beanie", "그레이 비니", "액세서리", "그레이", "겨울", "캐주얼", "gray beanie hat"),
    ("black-cap", "블랙 볼캡", "액세서리", "블랙", "사계절", "스포티", "black baseball cap"),
    ("check-scarf", "베이지 체크 머플러", "액세서리", "베이지", "겨울", "캐주얼", "plaid scarf"),
]
FIELDS = ("id", "name", "category", "color", "season", "style", "query")


def _photos() -> dict:
    if not CATALOG_JSON.exists():
        return {}
    return _read(CATALOG_JSON.stat().st_mtime)     # 스크립트로 다시 만들면 서버를 껐다 켜지 않아도 반영


@lru_cache(maxsize=2)
def _read(mtime: float) -> dict:
    return json.loads(CATALOG_JSON.read_text(encoding="utf-8"))


def entries() -> list[dict]:
    """스크립트가 처리한 아이템 (아직 안 돌렸으면 빈 목록).
    품질 기준을 통과한 사진이 없던 아이템은 photo=None으로 들어오고, 화면에서는 옷 그림으로 보여 준다."""
    photos = _photos()
    out = []
    for row in ITEMS:
        e = dict(zip(FIELDS, row))
        info = photos.get(e["id"])
        if not info:
            continue
        photo = info.get("photo")
        if photo is None or local_path(e["id"]).exists() or photo.get("source", "Pexels") in REMOTE_OK:
            out.append({**e, **info})
    return out


def find(item_id: str) -> dict | None:
    return next((e for e in entries() if e["id"] == item_id), None)


def local_path(item_id: str):
    return config.MEDIA_DIR / "catalog" / f"{item_id}.jpg"


def image_url(entry: dict) -> str | None:
    if not entry.get("photo"):
        return None
    return f"/media/catalog/{entry['id']}.jpg" if local_path(entry["id"]).exists() else entry["photo"]["src"]


def image_bytes(entry: dict) -> bytes | None:
    """옷장에 넣을 사진. 미리 받아 둔 파일이 있으면 그걸, 없으면 Pexels에서 받는다 (Pexels 주소만 허용)."""
    if not entry.get("photo"):
        return None
    path = local_path(entry["id"])
    if path.exists():
        return path.read_bytes()
    return fetch(entry["photo"]["src"], max_bytes=5 * 1024 * 1024, allowed_hosts=IMAGE_HOSTS).content


# 사진이 없는 옷에 붙일 기본 옷 사진을 고를 때, 이 정도까지는 같은 색으로 본다
NEAR_COLOR = {
    "화이트": ["화이트", "아이보리"], "아이보리": ["아이보리", "화이트"], "베이지": ["베이지", "아이보리", "카키"],
    "브라운": ["브라운", "베이지"], "블랙": ["블랙", "차콜"], "차콜": ["차콜", "블랙", "그레이"], "그레이": ["그레이", "차콜"],
    "네이비": ["네이비", "블루"], "블루": ["블루", "스카이블루", "네이비"], "스카이블루": ["스카이블루", "블루"],
}


# 세부 종류 — 이름에 이 단어가 있으면 그 종류. 사진을 붙일 때 세부 종류까지 같아야 한다
# (처음엔 종류+색만 봤더니 옥스퍼드 셔츠에 반팔 티, 롱패딩에 가죽 재킷, 로퍼에 샌들 사진이 붙었다)
KIND_WORDS = {
    "티셔츠": ["티셔츠", "반팔"], "셔츠": ["셔츠", "블라우스"], "니트": ["니트", "스웨터", "터틀넥"],
    "맨투맨": ["맨투맨", "스웨트셔츠"], "후드": ["후드"], "청바지": ["청바지", "데님 팬츠", "진"],
    "슬랙스": ["슬랙스", "정장 바지"], "면바지": ["치노", "면바지"], "와이드 팬츠": ["와이드"],
    "트레이닝 바지": ["트레이닝 바지", "조거"], "반바지": ["반바지", "쇼츠"], "스커트": ["스커트", "치마"],
    "트렌치코트": ["트렌치"], "코트": ["코트"], "패딩": ["패딩"], "블레이저": ["블레이저"], "재킷": ["재킷", "자켓"],
    "카디건": ["카디건", "가디건"], "플리스": ["플리스"], "바람막이": ["바람막이"],
    "스니커즈": ["스니커즈", "운동화"], "러닝화": ["러닝화"], "로퍼": ["로퍼"], "부츠": ["부츠"], "샌들": ["샌들"],
    "토트백": ["토트"], "에코백": ["에코백"], "크로스백": ["크로스백"], "백팩": ["백팩"],
    "비니": ["비니"], "볼캡": ["볼캡", "모자"], "머플러": ["머플러", "목도리"],
}


def kind_of(name: str) -> str | None:
    """이름에 들어 있는 가장 긴 종류 단어 (예: '트레이닝 쇼츠' → 반바지)"""
    name = name or ""
    best = None
    for kind, words in KIND_WORDS.items():
        for w in words:
            if w in name and (best is None or len(w) > best[1]):
                best = (kind, len(w))
    return best[0] if best else None


def match_photo(category: str, color: str, name: str = "") -> dict | None:
    """종류·세부 종류가 같고 색이 같거나 가까운, 사진이 있는 기본 옷. 세부 종류를 모르면 붙이지 않는다."""
    kind = kind_of(name)
    if kind is None:
        return None
    with_photo = [e for e in entries() if e.get("photo") and local_path(e["id"]).exists()
                  and e["category"] == category and kind_of(e["name"]) == kind]
    for c in NEAR_COLOR.get(color, [color]):
        for e in with_photo:
            if e["color"] == c:
                return e
    return None
