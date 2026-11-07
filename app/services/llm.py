"""
llm.py — 추천 프롬프트를 만들고 Groq API를 호출한다.

브라우저가 아니라 서버가 호출하므로 API 키가 사용자에게 노출되지 않는다.
옷장이 있으면 옷은 점수로 이미 고른 상태이고(app/services/outfits.py), LLM은 그 코디의 설명만 쓴다.
옷장이 비어 있으면 일반적인 코디 조언을 쓴다.
"""

import re

import httpx

from app import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMNotConfigured(Exception):
    """서버에 GROQ_API_KEY가 없다."""


class LLMError(Exception):
    """LLM 호출이 실패했다 (모델 지원 종료, 한도 초과, 네트워크 등)."""


def build_prompt(weather, mood, occasions, style, user) -> str:
    """옷장이 비었을 때: 일반적인 코디 조언"""
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

    return base + """

[추천 형식]
1. 상의, 하의, 아우터(필요할 때), 신발, 액세서리를 색상·소재까지 구체적으로 추천
2. 퍼스널 컬러와 체형에 맞는 색상·핏 팁
3. 날씨에 맞는 스타일링 팁 1가지
4. 300~500자, 친근한 말투"""


def build_explain_prompt(weather, mood, occasions, style, user, outfits) -> str:
    """점수로 이미 고른 코디들에 대해 설명만 부탁한다. 옷을 새로 고르게 하지 않는다."""
    lines = []
    for n, o in enumerate(outfits, 1):
        items = ", ".join(f"{SLOT_KO[s]} {it.name or it.color + ' ' + it.category}" for s, it in o.slots.items())
        lines.append(f"[{n}] {items}\n    규칙 분석: {' / '.join(o.reasons)}")
    return f"""당신은 친근한 패션 스타일리스트입니다. 아래 코디들은 고객의 옷장에서 날씨·상황·색 조합 점수로 이미 고른 것입니다.
옷을 바꾸거나 새로 추천하지 말고, 각 코디가 왜 오늘 괜찮은지와 입을 때 팁을 설명해 주세요.

[오늘] 체감 {weather.feels_like if weather.feels_like is not None else weather.temp:.0f}°C, {weather.desc} / 기분: {mood} / 상황: {", ".join(occasions) or "특별히 없음"} / 선호 스타일: {style}
[고객] 성별 {user.gender}, 체형 {user.body_type}, 퍼스널 컬러 {user.personal_color}

[코디]
{chr(10).join(lines)}

[출력 형식 — 반드시 지켜주세요]
코디마다 번호로 시작하는 한 단락(2~3문장, 120자 안팎)을 쓰세요. 예:
[1] 설명…
[2] 설명…
번호 단락 외에 다른 말은 쓰지 마세요."""


def parse_comments(text: str, n: int) -> list[str]:
    """"[1] …" 형식의 설명을 코디 순서대로 나눈다. 형식을 안 지켰으면 전체를 첫 코디 설명으로 쓴다."""
    parts = re.split(r"^\s*\[(\d)\]\s*", text.strip(), flags=re.M)
    found = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if not found:
        return [text.strip()] + [""] * (n - 1)
    return [found.get(i, "") for i in range(1, n + 1)]


SLOT_KO = {"top": "상의", "bottom": "하의", "outer": "아우터", "shoes": "신발", "accessory": "가방·소품"}


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
