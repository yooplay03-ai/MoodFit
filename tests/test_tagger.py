"""사진 자동 분류 — 색 추출은 실제로 계산하고, CLIP 모델은 가짜로 바꿔 API 동작만 확인한다."""

import io

from PIL import Image

from conftest import signup


def jpeg(color, bg=(250, 250, 250), size=200):
    """배경 위에 옷 모양(가운데 사각형)이 있는 사진을 만든다."""
    img = Image.new("RGB", (size, size), bg)
    img.paste(Image.new("RGB", (size // 2, size * 2 // 3), color), (size // 4, size // 6))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_dominant_color_ignores_background():
    from app.services.tagger import dominant_color
    navy_on_white = Image.open(io.BytesIO(jpeg((32, 42, 82))))
    color, share = dominant_color(navy_on_white)
    assert color == "네이비" and share > 0.8
    beige_on_gray = Image.open(io.BytesIO(jpeg((222, 201, 168), bg=(120, 120, 120))))
    assert dominant_color(beige_on_gray)[0] == "베이지"


def test_analyze_endpoint_returns_tags(client, monkeypatch):
    from app.services import tagger
    fake = {"category": {"value": "아우터", "confidence": 0.82, "scores": {}},
            "season": {"value": "가을", "confidence": 0.41, "scores": {}},
            "style": {"value": "미니멀", "confidence": 0.37, "scores": {}},
            "color": {"value": "베이지", "confidence": 0.9}}
    monkeypatch.setattr(tagger, "analyze", lambda data: fake)
    headers = signup(client)
    r = client.post("/api/wardrobe/analyze", headers=headers, files={"photo": ("c.jpg", jpeg((222, 201, 168)), "image/jpeg")})
    assert r.status_code == 200 and r.json()["category"]["value"] == "아우터"
    # 분석만 하고 옷장에는 저장하지 않는다
    assert client.get("/api/wardrobe", headers=headers).json() == []


def test_analyze_without_model_returns_503(client, monkeypatch):
    from app.services import tagger

    def unavailable(data):
        raise tagger.TaggerUnavailable("torch 없음")
    monkeypatch.setattr(tagger, "analyze", unavailable)
    headers = signup(client)
    r = client.post("/api/wardrobe/analyze", headers=headers, files={"photo": ("c.jpg", jpeg((0, 0, 0)), "image/jpeg")})
    assert r.status_code == 503


def test_analyze_rejects_broken_file(client):
    headers = signup(client)
    r = client.post("/api/wardrobe/analyze", headers=headers, files={"photo": ("c.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400            # 모델을 불러오기 전에 사진부터 연다
    assert client.post("/api/wardrobe/analyze", files={"photo": ("c.jpg", jpeg((0, 0, 0)), "image/jpeg")}).status_code == 401


def test_reanalyze_updates_saved_item(client, monkeypatch):
    from app.services import tagger
    tags = {"category": {"value": "아우터", "confidence": 0.9}, "season": {"value": "겨울", "confidence": 0.7},
            "style": {"value": "캐주얼", "confidence": 0.8}, "color": {"value": "스카이블루", "confidence": 0.6}}
    monkeypatch.setattr(tagger, "analyze", lambda data: tags)
    headers = signup(client)
    item = client.post("/api/wardrobe", headers=headers, data={"category": "미분류", "season": "여름", "color": "화이트"},
                       files={"photo": ("p.jpg", jpeg((150, 196, 232)), "image/jpeg")}).json()
    r = client.post(f"/api/wardrobe/{item['id']}/reanalyze", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert (body["category"], body["season"], body["color"]) == ("아우터", "겨울", "스카이블루")

    # 종류를 확신하지 못하면 종류는 그대로 둔다
    tags["category"] = {"value": "상의", "confidence": 0.3}
    assert client.post(f"/api/wardrobe/{item['id']}/reanalyze", headers=headers).json()["category"] == "아우터"

    no_photo = client.post("/api/wardrobe", headers=headers, data={"category": "상의"}).json()
    assert client.post(f"/api/wardrobe/{no_photo['id']}/reanalyze", headers=headers).status_code == 400
    other = signup(client, email="other@example.com")
    assert client.post(f"/api/wardrobe/{item['id']}/reanalyze", headers=other).status_code == 404
