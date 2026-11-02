"""추천 API — Groq 호출은 가짜 함수로 바꾸고, 프롬프트와 응답 처리만 확인한다."""

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


def test_picks_only_own_items_in_matching_slots(client, monkeypatch):
    headers = signup(client)
    top = add_item(client, headers, category="상의", style="포멀")
    bottom = add_item(client, headers, category="하의", style="포멀")
    other = add_item(client, signup(client, email="other@example.com"), category="신발")
    answer = (f"[PICK]\ntop: {top['id']}\nbottom: {top['id']}\nshoes: {other['id']}\nouter: 없음\n[/PICK]\n"
              "셔츠로 단정하게 입어 보세요.")
    seen = fake_llm(monkeypatch, answer)

    r = client.post("/api/recommend", headers=headers, json=BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    # 상의 슬롯만 인정: 하의 슬롯에 상의를 넣은 것, 남의 신발을 고른 것은 버린다
    assert [(p["slot"], p["item"]["id"]) for p in body["picks"]] == [("top", top["id"])]
    assert body["text"] == "셔츠로 단정하게 입어 보세요."
    assert body["used_wardrobe"] is True
    assert {c["item_id"] for c in body["candidates"]} == {top["id"], bottom["id"]}
    # 프롬프트에는 내 옷만 들어가고 남의 옷은 없다
    assert f"[{top['id']}]" in seen["prompt"] and f"[{other['id']}]" not in seen["prompt"]


def test_empty_wardrobe_gives_general_advice(client, monkeypatch):
    headers = signup(client)
    seen = fake_llm(monkeypatch, "네이비 니트에 슬랙스를 추천해요.")
    body = client.post("/api/recommend", headers=headers, json=BODY).json()
    assert body["picks"] == [] and body["used_wardrobe"] is False
    assert "[PICK]" not in seen["prompt"]


def test_llm_errors_map_to_status_codes(client, monkeypatch):
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
    bad_mood = {**BODY, "mood": "배고픔"}
    assert client.post("/api/recommend", headers=headers, json=bad_mood).status_code == 422
    assert client.post("/api/recommend", json=BODY).status_code == 401


def test_parse_picks_handles_brackets_and_missing_block():
    from app.services.llm import parse_picks
    picks, rest = parse_picks("[PICK]\ntop: [12]\nbottom: 없음\n[/PICK]\n설명")
    assert picks == {"top": 12} and rest == "설명"
    assert parse_picks("블록 없이 설명만") == ({}, "블록 없이 설명만")
