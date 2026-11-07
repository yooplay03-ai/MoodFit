"""
build_catalog.py — '기본 옷으로 빠르게 채우기'용 사진을 무료 사진 API로 모으고, 품질 기준을 통과한 것만 쓴다.

쇼핑몰을 긁는 대신 공식 API를 쓴 이유: 쇼핑몰 상품 사진은 저작권과 이용약관(자동 수집 금지) 문제가 있어 공개 포트폴리오에 쓸 수 없다.
  - Pixabay (기본): 키를 바로 발급. 사진을 내 서버에 내려받아 쓰는 것을 권장하고 원래 주소를 계속 쓰는 것(핫링크)은 금지.
                   출처 표시는 의무가 아니지만 화면에 Pixabay와 작가를 밝힌다. 1분에 100번까지 요청 가능. 'fashion' 분류로 거른다.
  - Pexels: 출처 표시 조건으로 허용. (2026-11 현재 새 키 발급이 일시 중단돼 있어 기본값을 Pixabay로 바꿨다)

처음 버전에서 배운 것
  "상품 사진다움 × 종류가 맞을 확률"이 가장 높은 후보를 고르고 AI로 종류를 다시 확인했더니 44개 중 42개가 '맞음'이었다.
  그런데 사진을 직접 보니 쓸 만한 건 5~6장뿐이었다 — 러그·옷걸이·벨트 버클, 빨간 '화이트 티', 모델 얼굴이 크게 나온 비니,
  두 아이템에 같은 사진. '종류 맞음' 하나로는 품질을 보장할 수 없었다.

그래서 지금은 후보마다 아래 기준을 모두 통과해야 쓰고, 통과한 후보가 없으면 사진 없이(화면에서는 옷 그림으로) 둔다.
  1. 상품 사진   FashionCLIP: "그 옷만 단색 배경에 놓인 상품 사진"이 사람·얼굴·여러 옷·풍경 문장보다 확률이 높고 0.5 이상
  2. 종류        FashionCLIP 종류 분류의 1등이 정의한 종류
  3. 색          배경을 지운 뒤 FashionCLIP 색 분류 상위 2개 안에 정의한 색(또는 가까운 색)이 있음
  4. 사람 없음   "사람이 있는 사진 / 사람 없는 상품 사진" 둘만 비교해 사람 확률 0.5 이하
                 (1번처럼 다섯 문장을 한꺼번에 비교하면 옷 설명이 너무 강해 모델 얼굴이 크게 나온 사진도 통과했다)
  6. 배경 제거 확인  지운 뒤 옷이 사진의 12% 이상 남아 있어야 함 (옷까지 지워지는 경우가 있었다)
  5. 중복 없음   다른 아이템이 이미 쓴 사진, 사람이 보고 탈락시킨(--reject) 사진은 쓰지 않음
  후보는 검색어에 'isolated'(배경이 없는 상품 사진에 많이 붙는 태그)를 붙여 30장 받고, 없으면 원래 검색어로 다시 찾는다.

결과: app/data/catalog.json (사진 id·작가·주소·출처·각 기준 점수·상황 점수), 사진 파일은 media/catalog/ (Git에 올리지 않음)

실행:  .env에 PIXABAY_API_KEY=키 (또는 PEXELS_API_KEY=키)  →  python scripts/build_catalog.py --redo
       python scripts/build_catalog.py                     (중간에 멈췄으면 이어서)
       python scripts/build_catalog.py --only black-tee,navy-knit
       python scripts/build_catalog.py --recheck --reject beige-knit,canvas-tote   (지금 기준으로 다시 검사 + 눈으로 본 불량 빼기)
       python scripts/build_catalog.py --pick black-tee=1234567     (사진을 직접 지정: 그 사이트의 사진 id, 기준 검사 생략)
"""

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402  (.env를 읽는다)
from app.services import catalog  # noqa: E402

CANDIDATES = 30
COLOR_CHECK_TOP = 6          # 1차 점수 상위 몇 장까지 배경을 지우고 색을 확인할지 (배경 제거가 장당 1초쯤 걸림)
DOWNLOAD_GAP = 0.8           # 사진 서버에 너무 빨리 요청하면 429로 막힌다
MIN_PRODUCT = 0.5
MIN_FILLED = 0.12           # 배경을 지운 사진에서 옷이 차지하는 비율 — 이보다 작으면 배경 제거가 옷까지 지운 것
MAX_PERSON = 0.5             # 두 번째 실행에서 모델 얼굴이 나온 사진이 13장 통과해, 사람 검사를 따로 둘만 비교하게 했다
PERSON_TEXTS = ["a photo of a person or a fashion model wearing clothes",
                "a product photo of clothing, shoes or a bag with no person in it"]
NOT_PRODUCT = ["a person wearing clothes", "a close-up photo of a person's face", "a photo of several different clothes",
               "a photo of a room, furniture or a street"]
# 사진 속 색을 이 정도까지는 같은 색으로 본다
NEAR_COLOR = {
    "화이트": {"화이트", "아이보리"}, "아이보리": {"아이보리", "화이트", "베이지"},
    "베이지": {"베이지", "아이보리", "브라운", "카키"}, "브라운": {"브라운", "베이지", "카키", "와인"},
    "블랙": {"블랙", "차콜"}, "차콜": {"차콜", "블랙", "그레이"}, "그레이": {"그레이", "차콜"},
    "네이비": {"네이비", "블루", "차콜"}, "블루": {"블루", "스카이블루", "네이비"}, "스카이블루": {"스카이블루", "블루"},
}


class Pixabay:
    name = "Pixabay"
    API = "https://pixabay.com/api/"

    def __init__(self, key):
        self.key = key
        self.client = httpx.Client(timeout=20)

    def _get(self, **params):
        r = self.client.get(self.API, params={"key": self.key, "image_type": "photo", "safesearch": "true", **params})
        r.raise_for_status()
        left = r.headers.get("X-RateLimit-Remaining")
        if left is not None and int(left) < 3:
            time.sleep(60)                               # 1분에 100번 한도 — 거의 다 쓰면 1분 쉰다
        return r.json()["hits"]

    def search(self, query):
        q = query.replace(" flat lay", "")
        for params in ({"q": f"{q} isolated", "category": "fashion"}, {"q": f"{q} isolated"}, {"q": q, "category": "fashion"}):
            hits = self._get(per_page=CANDIDATES, **params)
            if len(hits) >= 5:
                return [self.norm(h) for h in hits]
        return [self.norm(h) for h in hits]

    def by_id(self, photo_id):
        return [self.norm(h) for h in self._get(id=photo_id)]

    def norm(self, h):
        return {"id": h["id"], "url": h["pageURL"], "photographer": h["user"],
                "photographer_url": f"https://pixabay.com/users/{h['user']}-{h['user_id']}/",
                "small": h["webformatURL"], "large": h.get("largeImageURL") or h["webformatURL"], "source": self.name}


class Pexels:
    name = "Pexels"
    API = "https://api.pexels.com/v1"

    def __init__(self, key):
        self.client = httpx.Client(headers={"Authorization": key}, timeout=20)

    def _get(self, path, **params):
        r = self.client.get(f"{self.API}{path}", params=params)
        r.raise_for_status()
        left = r.headers.get("X-Ratelimit-Remaining")
        if left is not None and int(left) < 5:
            sys.exit("Pexels 시간당 요청 한도에 거의 닿았어요. 한 시간 뒤에 다시 실행하면 이어서 합니다.")
        return r.json()

    def search(self, query):
        return [self.norm(p) for p in self._get("/search", query=query, per_page=CANDIDATES, orientation="square")["photos"]]

    def by_id(self, photo_id):
        return [self.norm(self._get(f"/photos/{photo_id}"))]

    def norm(self, p):
        return {"id": p["id"], "url": p["url"], "photographer": p["photographer"], "photographer_url": p["photographer_url"],
                "small": p["src"]["medium"], "large": p["src"]["large"], "source": self.name}


def provider():
    if os.environ.get("PIXABAY_API_KEY"):
        return Pixabay(os.environ["PIXABAY_API_KEY"])
    if os.environ.get("PEXELS_API_KEY"):
        return Pexels(os.environ["PEXELS_API_KEY"])
    sys.exit(".env에 PIXABAY_API_KEY=키 를 넣어 주세요. (pixabay.com 가입 → pixabay.com/api/docs/ 에서 키 확인)")


def download(url, tries: int = 6) -> bytes:
    """사진 받기. 429(너무 많은 요청)면 서버가 알려 준 시간(없으면 10·20·40초…)만큼 쉬고 다시 시도한다."""
    for n in range(tries):
        time.sleep(DOWNLOAD_GAP)
        r = httpx.get(url, timeout=20)
        if r.status_code == 429 and n < tries - 1:
            # Pixabay는 Retry-After: 0을 보내기도 해서 그대로 따르면 바로 또 막힌다 → 최소 5·10·20초…는 쉰다
            wait = max(int(r.headers.get("Retry-After") or 0), 5 * 2 ** n)
            print(f"    사진 서버가 잠깐 막았어요(429). {wait}초 쉬고 다시 시도합니다…")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.content
    raise RuntimeError("계속 막혀서 받지 못했어요")


class Judge:
    """FashionCLIP으로 후보 사진을 평가한다. 모델이 없으면 기준 검사를 못 하므로 스크립트를 멈춘다."""

    def __init__(self):
        from app.services import tagger
        if not tagger.installed():
            sys.exit("사진 기준 검사에 FashionCLIP이 필요해요: pip install -r requirements-ml.txt")
        self.tagger = tagger
        self.torch, self.model, self.processor, self.banks, _ = tagger._load()

    def product(self, image: Image.Image, query: str) -> float:
        target = f"a product photo of a {query.replace(' flat lay', '')} alone on a plain background"
        inputs = self.processor(text=[target, *NOT_PRODUCT], images=image.convert("RGB"), return_tensors="pt", padding=True)
        with self.torch.no_grad():
            return float(self.model(**inputs).logits_per_image.softmax(dim=-1)[0][0])

    def person(self, image: Image.Image) -> float:
        """사람이 찍혔을 확률. 다섯 문장을 한꺼번에 비교하면 옷 설명 문장이 너무 강해 사람을 못 거르므로, 둘만 비교한다."""
        inputs = self.processor(text=PERSON_TEXTS, images=image.convert("RGB"), return_tensors="pt", padding=True)
        with self.torch.no_grad():
            return float(self.model(**inputs).logits_per_image.softmax(dim=-1)[0][0])

    def label(self, image: Image.Image, field: str) -> dict:
        return self.tagger.classify(self.tagger.image_embedding(image), *self.banks[field])[2]


def filled_ratio(cut_bytes: bytes) -> float:
    """배경 제거 결과에서 바탕색이 아닌 픽셀 비율"""
    img = Image.open(io.BytesIO(cut_bytes)).convert("RGB").resize((64, 64))
    bg = (244, 242, 237)                         # app/services/cutout.py의 바탕색
    return sum(1 for px in img.getdata() if sum(abs(a - b) for a, b in zip(px, bg)) > 30) / (64 * 64)


def choose(cands, item, judge, used_ids, cutout):
    """기준을 모두 통과한 후보 중 가장 좋은 것. 없으면 (None, 이유)"""
    first = []
    reasons = {"상품 사진 아님": 0, "사람이 찍힘": 0, "종류 다름": 0, "색 다름": 0, "배경 제거 실패": 0, "중복·제외": 0}
    for c in cands:
        if c["id"] in used_ids:
            reasons["중복·제외"] += 1
            continue
        try:
            img = Image.open(io.BytesIO(download(c["small"]))).convert("RGB")
        except Exception as e:
            print(f"    후보 {c['id']} 건너뜀: {e}")
            continue
        p_product = judge.product(img, item["query"])
        cats = judge.label(img, "category")
        if p_product < MIN_PRODUCT:
            reasons["상품 사진 아님"] += 1
            continue
        p_person = judge.person(img)
        if p_person > MAX_PERSON:
            reasons["사람이 찍힘"] += 1
            continue
        if max(cats, key=cats.get) != item["category"]:
            reasons["종류 다름"] += 1
            continue
        first.append((p_product * cats[item["category"]] * (1 - p_person), c, p_product, cats[item["category"]], p_person))
    first.sort(key=lambda x: -x[0])
    near = NEAR_COLOR.get(item["color"], {item["color"]})
    for _, c, p_product, p_cat, p_person in first[:COLOR_CHECK_TOP]:
        raw = download(c["large"])
        cut = cutout.make_cutout(raw) if cutout else None
        if cut is not None and filled_ratio(cut) < MIN_FILLED:   # 린넨 셔츠처럼 옷까지 지워진 경우
            reasons["배경 제거 실패"] += 1
            continue
        colors = judge.label(Image.open(io.BytesIO(cut or raw)), "color")
        top2 = sorted(colors, key=colors.get, reverse=True)[:2]
        if near & set(top2):
            return c, raw, cut, {"product": round(p_product, 3), "person": round(p_person, 3),
                                 "category": round(p_cat, 3), "colors": top2}
        reasons["색 다름"] += 1
    return None, ", ".join(f"{k} {v}" for k, v in reasons.items() if v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="쉼표로 구분한 아이템 id만 다시 만든다")
    ap.add_argument("--pick", action="append", default=[], help="id=사진id 로 사진을 직접 지정 (기준 검사 생략)")
    ap.add_argument("--redo", action="store_true", help="이미 만든 아이템도 처음부터 다시")
    ap.add_argument("--reject", help="쉼표로 구분한 아이템 id: 지금 사진을 사람이 보고 탈락시킴 → 그 사진은 다시 안 뽑고 새로 찾는다")
    ap.add_argument("--recheck", action="store_true", help="이미 통과한 사진을 지금 기준(사람 검사 포함)으로 다시 검사해 떨어지면 새로 찾는다")
    args = ap.parse_args()

    api = provider()
    print(f"사진 출처: {api.name}")
    only = set(args.only.split(",")) if args.only else None
    picks = dict(p.split("=", 1) for p in args.pick)

    out_dir = config.MEDIA_DIR / "catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {} if args.redo else (json.loads(catalog.CATALOG_JSON.read_text(encoding="utf-8")) if catalog.CATALOG_JSON.exists() else {})
    judge = Judge()
    try:
        from app.services import cutout
        cutout = cutout if config.PHOTO_CUTOUT else None
    except ImportError:
        cutout = None

    redo = set()
    # 사람이 눈으로 보고 탈락시킨 사진: 그 아이템의 제외 목록에 넣고 다시 찾는다
    for item_id in (args.reject.split(",") if args.reject else []):
        e = data.get(item_id.strip())
        if e and e.get("photo"):
            e.setdefault("banned", []).append(e["photo"]["id"])
            redo.add(item_id.strip())
    # 이미 통과한 사진을 지금 기준으로 다시 검사 (내려받아 둔 원본으로 — 네트워크 요청 없음)
    if args.recheck:
        for item_id, e in data.items():
            orig = out_dir / f"{item_id}_orig.jpg"
            if item_id in redo or not e.get("photo") or not orig.exists() or e.get("checks", {}).get("picked_by_hand"):
                continue
            p_person = judge.person(Image.open(orig))
            if p_person > MAX_PERSON:
                e.setdefault("banned", []).append(e["photo"]["id"])
                redo.add(item_id)
                print(f"  {item_id:<20} 다시 검사: 사람이 찍힘({p_person:.2f}) → 새로 찾음")

    def save():   # 아이템마다 바로 저장 — 중간에 멈춰도 여기까지 한 건 남는다
        catalog.CATALOG_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    for row in catalog.ITEMS:
        item = dict(zip(catalog.FIELDS, row))
        if only and item["id"] not in only:
            continue
        if not (only or item["id"] in picks or item["id"] in redo) and item["id"] in data:
            continue
        banned = data.get(item["id"], {}).get("banned", [])
        used_ids = {e["photo"]["id"] for k, e in data.items() if k != item["id"] and e.get("photo")} | set(banned)
        for old in (out_dir / f"{item['id']}.jpg", out_dir / f"{item['id']}_orig.jpg"):
            old.unlink(missing_ok=True)

        if item["id"] in picks:
            cands = api.by_id(picks[item["id"]])
            if not cands:
                print(f"  {item['id']}: 사진 {picks[item['id']]}를 찾지 못했어요")
                continue
            best, raw = cands[0], download(cands[0]["large"])
            cut = cutout.make_cutout(raw) if cutout else None
            checks = {"picked_by_hand": True}
        else:
            cands = api.search(item["query"])
            best, raw_or_reason, *rest = choose(cands, item, judge, used_ids, cutout) if cands else (None, "검색 결과 없음")
            if best is None:
                data[item["id"]] = {"photo": None, "rejected": raw_or_reason, "banned": banned}
                save()
                print(f"  {item['id']:<20} ✕ 기준을 통과한 사진 없음 ({raw_or_reason}) → 그림으로 표시")
                continue
            raw, (cut, checks) = raw_or_reason, rest

        (out_dir / f"{item['id']}_orig.jpg").write_bytes(raw)
        (out_dir / f"{item['id']}.jpg").write_bytes(cut or raw)
        tags = judge.tagger.analyze(raw)
        data[item["id"]] = {
            "photo": {"id": best["id"], "url": best["url"], "photographer": best["photographer"],
                      "photographer_url": best["photographer_url"], "src": best["large"], "source": best["source"]},
            "checks": checks, "occasion_fit": tags.get("occasion_fit"), "banned": banned,
        }
        save()
        print(f"  {item['id']:<20} ✓ {best['source']} 사진 {best['id']} ({best['photographer']}) {checks}")

    ok = [k for k, e in data.items() if e.get("photo")]
    no = [k for k, e in data.items() if not e.get("photo")]
    print(f"\n사진 통과 {len(ok)}개, 그림으로 표시 {len(no)}개 (전체 {len(catalog.ITEMS)}개 중 {len(data)}개 처리)")
    if no:
        print("그림으로 표시:", ", ".join(no))
    print("media/catalog 사진을 직접 훑어보고, 이상한 건 --reject 아이템id 로 빼거나 --pick 아이템id=사진id 로 지정하세요.")


if __name__ == "__main__":
    main()
