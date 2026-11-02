"""
llm.py — 추천 프롬프트를 만들고 Groq API를 호출한다.

브라우저가 아니라 서버가 호출하므로 API 키가 사용자에게 노출되지 않는다.
LLM 답의 맨 앞 [PICK] 블록에서 고른 옷의 id를 읽어, 실제 그 사용자의 옷인지 서버에서 다시 확인한다.
"""

import re

import httpx

from app import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SLOTS = ("top", "bottom", "outer", "shoes", "accessory")
SLOT_CATEGORY = {"top": "상의", "bottom": "하의", "outer": "아우터", "shoes": "신발", "accessory": ("액세서리", "가방")}


class LLMNotConfigured(Exception):
    """서버에 GROQ_API_KEY가 없다."""


class LLMError(Exception):
    """LLM 호출이 실패했다 (모델 지원 종료, 한도 초과, 네트워크 등)."""


def build_prompt(weather, mood, occasions, style, user, wardrobe_text: str | None) -> str:
    height = f"{user.height_cm}cm" if user.height_cm else "미입력"
    weight = f"{user.weight_kg}kg" if user.weight_kg else "미입력"
    base = f"""당신은 전문 패션 스타일리스트입니다. 아래 정보를 바탕으로 오늘의 코디를 추천해주세요.

[오늘의 날씨]
- 기온: {weather.temp:.0f}°C / 날씨: {weather.desc} / 습도: {weather.humidity}%

[고객 프로필]
- 성별: {user.gender} / 체형: {user.body_type} / 키: {height} / 몸무게: {weight}
- 피부톤: {user.skin_tone} / 퍼스널 컬러: {user.personal_color}

[오늘의 컨디션]
- 기분: {mood}
- 상황: {", ".join(occasions) or "특별히 정해지지 않음"}
- 선호 스타일: {style}"""

    if wardrobe_text is None:
        return base + """

[추천 형식]
1. 상의, 하의, 아우터(필요할 때), 신발, 액세서리를 색상·소재까지 구체적으로 추천
2. 퍼스널 컬러와 체형에 맞는 색상·핏 팁
3. 날씨에 맞는 스타일링 팁 1가지
4. 300~500자, 친근한 말투"""

    return base + f"""

[내 옷장 — 실제 보유 아이템 (이 목록 밖의 옷은 추천하지 마세요)]
{wardrobe_text}
※ ★는 오늘 기온·상황·기분 기준으로 카테고리별로 미리 계산한 1순위입니다. 안 어울릴 이유가 없다면 ★를 우선 고르세요.
※ ⚠는 오늘 기온과 계절이 크게 안 맞는 아이템입니다. 대안이 없을 때만 고르고, 고른다면 설명에서 이 점을 짚어주세요.

[출력 형식 — 반드시 지켜주세요]
가장 먼저 아래 블록을 채우세요. 괄호 안 숫자 ID만 쓰고, 마땅한 옷이 없으면 "없음"이라고 쓰세요.
[PICK]
top: <ID 또는 없음>
bottom: <ID 또는 없음>
outer: <ID 또는 없음>
shoes: <ID 또는 없음>
accessory: <ID 또는 없음>
[/PICK]

그 다음 줄부터 이 조합을 고른 이유, 퍼스널 컬러·체형에 맞는 팁, 날씨 팁을 200~400자로 친근하게 쓰세요.
설명 부분에는 ID나 대괄호를 다시 쓰지 마세요."""


def parse_picks(text: str) -> tuple[dict[str, int], str]:
    """[PICK] 블록에서 slot → 옷 id를 읽고, 블록을 뺀 나머지 설명을 돌려준다."""
    m = re.search(r"\[PICK\](.*?)\[/PICK\]", text, re.S | re.I)
    if not m:
        return {}, text.strip()
    picks = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*(top|bottom|outer|shoes|accessory)\s*:\s*\[?(\d+)\]?", line, re.I)
        if mm:
            picks[mm.group(1).lower()] = int(mm.group(2))
    rest = (text[: m.start()] + text[m.end():]).strip()
    return picks, rest


def ask_groq(prompt: str, timeout: float = 40.0) -> str:
    if not config.GROQ_API_KEY:
        raise LLMNotConfigured("GROQ_API_KEY가 설정되지 않았습니다.")
    try:
        res = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": config.GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_completion_tokens": 2000,
                "reasoning_effort": "low",   # gpt-oss는 답 전에 생각하는 토큰을 쓴다. 코디 추천은 짧게 생각해도 충분
            },
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"네트워크 오류: {type(e).__name__}") from e
    if res.status_code != 200:
        try:
            msg = res.json().get("error", {}).get("message", "")
        except ValueError:
            msg = res.text[:200]
        raise LLMError(f"Groq {res.status_code}: {msg}")
    return res.json()["choices"][0]["message"]["content"] or ""
