"""옷을 빨리 채우는 두 가지 — 기본 옷 카탈로그(Pexels), 상품 링크 가져오기"""

import io
import json

import pytest
from PIL import Image

from conftest import signup


def jpeg_bytes(color=(30, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (60, 60), color).save(buf, "JPEG")
    return buf.getvalue()


# ── 안전한 주소 받기 (내부망 접근 차단) ──────────────────────
@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://localhost:8000/", "http://10.0.0.5/",
                                 "http://192.168.0.1/", "http://169.254.169.254/latest/meta-data/",
                                 "file:///etc/passwd", "ftp://example.com/a.jpg", "http://[::1]/"])
def test_blocks_internal_and_odd_urls(url):
    from app.services.fetch import FetchError, check_public
    with pytest.raises(FetchError):
        check_public(url)


def test_allowed_hosts_only():
    from app.services.fetch import FetchError, check_public
    with pytest.raises(FetchError):
        check_public("https://example.com/x.jpg", allowed_hosts={"images.pexels.com"})


# ── 상품 페이지에서 사진·이름 읽기 ──────────────────────────
def test_parse_product_page():
    from app.services.product_link import parse_page
    page = """<html><head><title>무시해도 되는 제목</title>
      <meta property="og:title" content="[무료배송] 오버핏 울 블렌드 니트 - 네이비 | 무신사">
      <meta property="og:image" content="/images/goods/123_big.jpg"></head></html>"""
    image, title = parse_page(page, "https://www.example-shop.com/goods/123")
    assert image == "https://www.example-shop.com/images/goods/123_big.jpg"
    assert title == "오버핏 울 블렌드 니트 - 네이비"


def test_import_link_endpoint(client, monkeypatch):
    from app.services import product_link
    from app.services.fetch import FetchError
    headers = signup(client)
    monkeypatch.setattr(product_link, "read_product", lambda url: {
        "title": "블랙 와이드 슬랙스", "image": jpeg_bytes(), "content_type": "image/jpeg", "image_url": "x"})
    r = client.post("/api/wardrobe/import-link", headers=headers, json={"url": "https://shop.example.com/goods/1"})
    assert r.status_code == 200
    assert r.json()["title"] == "블랙 와이드 슬랙스" and r.json()["image"].startswith("data:image/jpeg;base64,")

    def blocked(url):
        raise FetchError("페이지를 열지 못했어요 (HTTP 403).")
    monkeypatch.setattr(product_link, "read_product", blocked)
    r = client.post("/api/wardrobe/import-link", headers=headers, json={"url": "https://shop.example.com/goods/1"})
    assert r.status_code == 400 and "403" in r.json()["detail"]


def test_link_source_is_saved(client):
    headers = signup(client)
    r = client.post("/api/wardrobe", headers=headers, data={"category": "하의", "source": "link"},
                    files={"photo": ("p.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.json()["source"] == "link"


# ── 기본 옷 카탈로그 ────────────────────────────────────────
@pytest.fixture
def fake_catalog(client, tmp_path, monkeypatch):
    from app import config
    from app.services import catalog
    path = tmp_path / "catalog.json"
    photo = {"id": 1, "url": "https://www.pexels.com/photo/1/", "photographer": "Kim",
             "photographer_url": "https://www.pexels.com/@kim", "src": "https://images.pexels.com/photos/1/a.jpeg"}
    path.write_text(json.dumps({
        "white-tee": {"photo": photo, "occasion_fit": {"출근": 0.2, "운동": 0.3}},
        "black-slacks": {"photo": {**photo, "id": 2}, "occasion_fit": {"출근": 0.6, "운동": 0.05}},
    }), encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_JSON", path)
    (config.MEDIA_DIR / "catalog").mkdir(parents=True, exist_ok=True)
    (config.MEDIA_DIR / "catalog" / "white-tee.jpg").write_bytes(jpeg_bytes((250, 250, 250)))
    monkeypatch.setattr(catalog, "fetch", lambda url, **kw: type("R", (), {"content": jpeg_bytes()})())
    return catalog


def test_catalog_lists_only_items_with_photos(client, fake_catalog):
    headers = signup(client)
    items = client.get("/api/wardrobe/catalog", headers=headers).json()
    assert [i["id"] for i in items] == ["white-tee", "black-slacks"]
    assert items[0]["image_url"] == "/media/catalog/white-tee.jpg"           # 미리 받아 둔 파일
    assert items[1]["image_url"].startswith("https://images.pexels.com/")     # 없으면 Pexels 주소
    assert items[0]["photographer"] == "Kim"


def test_add_from_catalog(client, fake_catalog):
    headers = signup(client)
    r = client.post("/api/wardrobe/from-catalog", headers=headers, json={"ids": ["white-tee", "black-slacks", "white-tee"]})
    assert r.status_code == 201
    added = r.json()
    assert len(added) == 2                                                     # 같은 걸 두 번 골라도 한 번
    slacks = next(i for i in added if i["name"] == "블랙 슬랙스")
    assert slacks["source"] == "catalog:black-slacks" and slacks["photo_url"]
    assert slacks["season_check"] is False                                    # 정의된 계절이라 확인 불필요
    from app.db import SessionLocal
    from app.models import ClothingItem
    with SessionLocal() as db:
        assert json.loads(db.get(ClothingItem, slacks["id"]).occasion_fit)["출근"] == 0.6
    assert client.post("/api/wardrobe/from-catalog", headers=headers, json={"ids": ["nope"]}).status_code == 404


def test_pixabay_items_need_local_file(client, tmp_path, monkeypatch):
    """Pixabay는 핫링크 금지라 내려받은 파일이 없으면 목록에서 뺀다 (Pexels는 원래 주소로 보여 줌)."""
    from app import config
    from app.services import catalog
    path = tmp_path / "c.json"
    base = {"url": "u", "photographer": "p", "photographer_url": "pu", "src": "https://cdn.pixabay.com/x.jpg"}
    path.write_text(json.dumps({
        "white-tee": {"photo": {**base, "id": 1, "source": "Pixabay"}},
        "black-tee": {"photo": {**base, "id": 2, "source": "Pixabay"}},
    }), encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_JSON", path)
    (config.MEDIA_DIR / "catalog").mkdir(parents=True, exist_ok=True)
    (config.MEDIA_DIR / "catalog" / "black-tee.jpg").write_bytes(jpeg_bytes())
    items = client.get("/api/wardrobe/catalog", headers=signup(client)).json()
    assert [i["id"] for i in items] == ["black-tee"] and items[0]["source"] == "Pixabay"


def test_catalog_item_without_approved_photo_uses_illustration(client, tmp_path, monkeypatch):
    """품질 기준을 통과한 사진이 없던 아이템도 목록에 나오고(그림), 옷장에 넣으면 사진 없이 들어간다."""
    from app.services import catalog
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"linen-shirt": {"photo": None, "rejected": "색 다름 3"}}), encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_JSON", path)
    headers = signup(client)
    items = client.get("/api/wardrobe/catalog", headers=headers).json()
    assert items[0]["id"] == "linen-shirt" and items[0]["image_url"] is None
    added = client.post("/api/wardrobe/from-catalog", headers=headers, json={"ids": ["linen-shirt"]}).json()
    assert added[0]["photo_url"] is None and added[0]["source"] == "catalog:linen-shirt"


def test_match_photo_needs_same_kind(fake_catalog):
    """사진 없는 옷에 붙일 기본 옷 사진: 세부 종류까지 같아야 한다 (셔츠에 티셔츠 사진을 붙이지 않음)."""
    from app.services.catalog import kind_of, match_photo
    assert kind_of("트레이닝 쇼츠") == "반바지" and kind_of("블랙 가죽 재킷") == "재킷"
    assert match_photo("상의", "화이트", "반팔 티셔츠")["id"] == "white-tee"     # 사진 파일이 있는 기본 옷
    assert match_photo("상의", "화이트", "옥스퍼드 셔츠") is None               # 셔츠 ≠ 티셔츠
    assert match_photo("상의", "블랙", "반팔 티셔츠") is None                  # 색이 다름
