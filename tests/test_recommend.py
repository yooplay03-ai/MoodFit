"""추천 API — Groq 호출은 가짜 함수로 바꾸고, 코디 조합·응답 처리만 확인한다."""

import json

from conftest import add_item, signup

BODY = {"weather": {"temp": 14, "desc": "맑음", "humidity": 50}, "mood": "진지함", "occasions": ["출근"], "style": "미니멀"}


def fake_llm(monkeypatch, answer):
    from app.services import llm
    seen = {}

    def fake(prompt, timeout=40.0):
        seen["prompt"] = prompt
        return answer
    monkeypatch.setattr(llm, "ask_groq", fake)
    return seen


def test_recommends_outfits_from_own_wardrobe(client, monkeypatch):
    headers = signup(client)
    top = add_item(client, headers, category="상의", color="화이트", name="흰 셔츠")
    bottom = add_item(client, headers, category="하의", color="블랙", name="검정 슬랙스")
    outer = add_item(client, headers, category="아우터", color="베이지", name="트렌치코트")
    other = add_item(client, signup(client, email="other@example.com"), category="상의", name="남의 티셔츠")
    seen = fake_llm(monkeypatch, "[1] 흰 셔츠와 검정 슬랙스는 출근 정석이에요.")

    r = client.post("/api/recommend", headers=headers, json=BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    best = body["outfits"][0]
    # 14도는 아우터 필수 → 상의·하의·아우터가 모두 들어간다
    assert {(p["slot"], p["item"]["id"]) for p in best["picks"]} == {("top", top["id"]), ("bottom", bottom["id"]), ("outer", outer["id"])}
    assert best["comment"] == "흰 셔츠와 검정 슬랙스는 출근 정석이에요."
    assert set(best["breakdown"]) == {"weather", "occasion", "color", "style", "fresh"}
    assert any("무채색" in r or "뉴트럴" in r for r in best["reasons"])
    assert body["used_wardrobe"] and body["llm_used"]
    # LLM에는 고른 코디만 설명을 부탁하고, 남의 옷은 들어가지 않는다
    assert "흰 셔츠" in seen["prompt"] and "남의 티셔츠" not in seen["prompt"]
    assert other["id"] not in {p["item"]["id"] for o in body["outfits"] for p in o["picks"]}


def test_outfits_still_shown_when_llm_fails(client, monkeypatch):
    from app.services import llm
    headers = signup(client)
    add_item(client, headers, category="상의")
    add_item(client, headers, category="하의")

    def broken(prompt, timeout=40.0):
        raise llm.LLMError("Groq 429")
    monkeypatch.setattr(llm, "ask_groq", broken)
    body = client.post("/api/recommend", headers=headers, json=BODY).json()
    assert len(body["outfits"]) == 1 and body["llm_used"] is False and body["outfits"][0]["reasons"]


def test_empty_wardrobe_gives_general_advice(client, monkeypatch):
    headers = signup(client)
    seen = fake_llm(monkeypatch, "네이비 니트에 슬랙스를 추천해요.")
    body = client.post("/api/recommend", headers=headers, json=BODY).json()
    assert body["outfits"] == [] and body["used_wardrobe"] is False
    assert body["text"] == "네이비 니트에 슬랙스를 추천해요."


def test_llm_errors_map_to_status_codes_without_wardrobe(client, monkeypatch):
    from app.services import llm
    headers = signup(client)

    def not_configured(prompt, timeout=40.0):
        raise llm.LLMNotConfigured()
    monkeypatch.setattr(llm, "ask_groq", not_configured)
    assert client.post("/api/recommend", headers=headers, json=BODY).status_code == 503

    def broken(prompt, timeout=40.0):
        raise llm.LLMError("Groq 404: model_not_found")
    monkeypatch.setattr(llm, "ask_groq", broken)
    assert client.post("/api/recommend", headers=headers, json=BODY).status_code == 502


def test_invalid_input_rejected(client):
    headers = signup(client)
    assert client.post("/api/recommend", headers=headers, json={**BODY, "mood": "배고픔"}).status_code == 422
    assert client.post("/api/recommend", json=BODY).status_code == 401


def test_sample_and_feedback_are_saved(client):
    from app.db import SessionLocal
    from app.models import OutfitFeedback
    headers = signup(client)
    top = add_item(client, headers, category="상의")
    bottom = add_item(client, headers, category="하의")
    sample = client.post("/api/recommend/sample", headers=headers, json=BODY).json()["outfit"]
    assert {p["slot"] for p in sample["picks"]} >= {"top", "bottom"}

    fb = {**BODY, "item_ids": {"top": top["id"], "bottom": bottom["id"]}, "source": "random", "rating": -1}
    r = client.post("/api/recommend/feedback", headers=headers, json=fb)
    assert r.status_code == 201 and r.json()["count"] == 1
    with SessionLocal() as db:
        row = db.query(OutfitFeedback).one()
        assert row.rating == -1 and row.source == "random" and json.loads(row.item_ids)["top"] == top["id"]
        assert row.score is not None

    other = signup(client, email="other@example.com")
    assert client.post("/api/recommend/feedback", headers=other, json=fb).status_code == 404   # 남의 옷은 평가 불가


def test_parse_comments():
    from app.services.llm import parse_comments
    assert parse_comments("[1] 첫째\n[2] 둘째", 3) == ["첫째", "둘째", ""]
    assert parse_comments("형식 없이 쓴 설명", 2) == ["형식 없이 쓴 설명", ""]
