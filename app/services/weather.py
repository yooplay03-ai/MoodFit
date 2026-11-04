"""
weather.py — 지금 날씨를 가져온다.

기상청 단기예보(공공데이터포털) 키가 있으면 기상청을 쓰고, 없으면 키가 필요 없는 Open-Meteo를 쓴다.
(공공데이터포털 키는 발급·승인 절차가 있어, 처음 실행하는 사람도 날씨가 바로 나오게 하기 위함)

기상청:

- 위도·경도를 기상청 격자 좌표(nx, ny)로 바꾼다 (기상청 제공 Lambert 투영 공식).
- 예보는 하루 8번(02, 05, 08 … 23시) 발표되고 10분 뒤부터 조회되므로, 가장 최근 발표 시각을 계산해 요청한다.
- 같은 격자·같은 발표 시각은 결과가 같으므로 30분 동안 서버 메모리에 저장해 두고 재사용한다.
"""

import math
import re
import time
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone

import httpx

from app import config

KST = timezone(timedelta(hours=9))
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
CACHE_SECONDS = 30 * 60
_cache: dict[tuple, tuple[float, dict]] = {}

CITIES = {
    "서울": (37.5665, 126.9780), "수원": (37.2636, 127.0286), "인천": (37.4563, 126.7052),
    "부산": (35.1796, 129.0756), "대구": (35.8714, 128.6014), "대전": (36.3504, 127.3845),
    "광주": (35.1595, 126.8526), "울산": (35.5384, 129.3114), "세종": (36.4801, 127.2890),
    "제주": (33.4996, 126.5312), "강릉": (37.7519, 128.8761), "전주": (35.8242, 127.1480),
    "청주": (36.6424, 127.4890), "창원": (35.2280, 128.6811), "고양": (37.6584, 126.8320),
    "성남": (37.4201, 127.1264), "용인": (37.2411, 127.1776), "안양": (37.3942, 126.9568),
    "의정부": (37.7380, 127.0337), "춘천": (37.8813, 127.7298),
}


class WeatherNotConfigured(Exception):
    pass


class WeatherError(Exception):
    pass


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위도·경도 → 기상청 격자 (nx, ny)"""
    re_, grid = 6371.00877, 5.0
    slat1, slat2, olon, olat = map(math.radians, (30.0, 60.0, 126.0, 38.0))
    xo, yo = 43, 136
    re_ /= grid
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5))
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn * math.cos(slat1) / sn
    ro = re_ * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re_ * sf / math.tan(math.pi * 0.25 + math.radians(lat) * 0.5) ** sn
    theta = math.radians(lon) - olon
    if theta > math.pi:
        theta -= 2 * math.pi
    if theta < -math.pi:
        theta += 2 * math.pi
    theta *= sn
    return int(ra * math.sin(theta) + xo + 0.5), int(ro - ra * math.cos(theta) + yo + 0.5)


def latest_base_time(now: datetime) -> tuple[str, str]:
    """지금 조회할 수 있는 가장 최근 예보 발표 시각 (base_date, base_time). now는 한국 시간."""
    for hour in (23, 20, 17, 14, 11, 8, 5, 2):
        issued = now.replace(hour=hour, minute=10, second=0, microsecond=0)
        if now >= issued:
            return issued.strftime("%Y%m%d"), f"{hour:02d}00"
    prev = now - timedelta(days=1)             # 00:00~02:09는 전날 23시 발표분
    return prev.strftime("%Y%m%d"), "2300"


def wind_chill(temp_c: float, wind_ms: float) -> float:
    """기상청 겨울철 체감온도. 기온 10도 이하, 풍속 4.8km/h 이상일 때만 적용하고 아니면 기온 그대로."""
    v = wind_ms * 3.6
    if temp_c > 10 or v < 4.8:
        return temp_c
    return 13.12 + 0.6215 * temp_c - 11.37 * v ** 0.16 + 0.3965 * v ** 0.16 * temp_c


def describe(pty: int, sky: int) -> str:
    if pty in (1, 4):
        return "비" if pty == 1 else "소나기"
    if pty == 2:
        return "비/눈"
    if pty == 3:
        return "눈"
    return {1: "맑음", 3: "구름많음", 4: "흐림"}.get(sky, "맑음")


def pick_current(items: list[dict], now: datetime) -> dict[str, str]:
    """항목(TMP, REH, …)마다 지금 시각 이후 가장 가까운 예보 값을 고른다. 없으면 가장 늦은 값."""
    target = now.strftime("%Y%m%d%H00")
    by_cat: dict[str, list[tuple[str, str]]] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append((it["fcstDate"] + it["fcstTime"], it["fcstValue"]))
    current = {}
    for cat, values in by_cat.items():
        values.sort()
        upcoming = [v for when, v in values if when >= target]
        current[cat] = upcoming[0] if upcoming else values[-1][1]
    return current


def parse_forecast(items: list[dict], now: datetime) -> dict:
    cur = pick_current(items, now)
    temp = float(cur.get("TMP", 15))
    wind = float(cur.get("WSD", 0))
    return {
        "temp": round(temp),
        "desc": describe(int(cur.get("PTY", 0)), int(cur.get("SKY", 1))),
        "humidity": int(cur.get("REH", 50)),
        "feels_like": round(wind_chill(temp, wind)),
        "rain_prob": int(cur.get("POP", 0)),
    }


KEY_HELP = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "등록되지 않은 키 — 활용신청 승인 후 1~2시간 뒤부터 쓸 수 있어요. .env의 키를 다시 복사해 보세요",
    "SERVICE_ACCESS_DENIED_ERROR": "이 API(단기예보) 활용신청이 안 된 키예요",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "오늘 호출 한도를 넘었어요",
}


def xml_error(text: str) -> str:
    """기상청이 XML로 보낸 오류 응답에서 원인 코드를 찾아 한국어 설명을 붙인다."""
    for tag in ("returnAuthMsg", "resultMsg", "errMsg"):
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", text)
        if m:
            code = m.group(1).strip()
            return f"{code} — {KEY_HELP[code]}" if code in KEY_HELP else code
    return "알 수 없는 응답"


# Open-Meteo 날씨 코드(WMO) → 화면 설명. 비·눈 여부가 추천에 중요하므로 그 둘을 먼저 구분한다.
def wmo_describe(code: int) -> str:
    if code in (71, 73, 75, 77, 85, 86):
        return "눈"
    if code in (80, 81, 82):
        return "소나기"
    if 51 <= code <= 67 or code >= 95:
        return "비"
    if code in (45, 48):
        return "흐림"            # 안개
    return {0: "맑음", 1: "맑음", 2: "구름많음", 3: "흐림"}.get(code, "맑음")


def parse_open_meteo(data: dict) -> dict:
    cur = data["current"]
    hourly = data.get("hourly", {})
    hour = cur["time"][:13] + ":00"             # "2026-11-04T21:15" → 그 시각의 강수확률
    probs = dict(zip(hourly.get("time", []), hourly.get("precipitation_probability", [])))
    return {
        "temp": round(cur["temperature_2m"]),
        "desc": wmo_describe(int(cur.get("weather_code", 0))),
        "humidity": int(cur.get("relative_humidity_2m", 50)),
        "feels_like": round(cur.get("apparent_temperature", cur["temperature_2m"])),
        "rain_prob": int(probs.get(hour) or 0),
    }


def get_open_meteo(lat: float, lon: float) -> dict:
    key = ("open-meteo", round(lat, 2), round(lon, 2), datetime.now(KST).strftime("%Y%m%d%H"))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    params = {"latitude": lat, "longitude": lon, "timezone": "Asia/Seoul", "forecast_days": 1,
              "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code",
              "hourly": "precipitation_probability"}
    try:
        res = httpx.get(OPEN_METEO_URL, params=params, timeout=10)
        res.raise_for_status()
        result = parse_open_meteo(res.json())
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise WeatherError(f"Open-Meteo 응답을 읽지 못했습니다: {type(e).__name__}") from e
    result["source"] = "Open-Meteo"
    _cache[key] = (time.time(), result)
    return result


def get_weather(lat: float, lon: float, now: datetime | None = None) -> dict:
    if not config.KMA_API_KEY:
        return get_open_meteo(lat, lon)      # 기상청 키가 없으면 키 없는 날씨 API로
    now = now or datetime.now(KST)
    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = latest_base_time(now)
    key = (nx, ny, base_date, base_time, now.strftime("%H"))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    # 공공데이터포털 키는 'Encoding'(%2B 등 포함)과 'Decoding' 두 가지가 있다.
    # httpx가 한 번 더 인코딩하므로, 어느 쪽을 넣어도 되게 먼저 풀어서(Decoding 키로 만들어) 보낸다.
    params = {"serviceKey": unquote(config.KMA_API_KEY.strip()), "numOfRows": 300, "pageNo": 1, "dataType": "JSON",
              "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny}
    try:
        res = httpx.get(URL, params=params, timeout=10)
    except httpx.HTTPError as e:
        raise WeatherError(f"기상청에 연결하지 못했습니다: {type(e).__name__}") from e
    try:
        data = res.json()
    except ValueError:
        # 키 오류 등은 JSON을 요청해도 XML로 온다 → 그 안의 오류 메시지를 꺼내 보여 준다
        raise WeatherError(f"기상청 오류: {xml_error(res.text)} (HTTP {res.status_code})")
    items = (data.get("response", {}).get("body") or {}).get("items", {}).get("item")
    if not items:
        msg = data.get("response", {}).get("header", {}).get("resultMsg", "데이터 없음")
        raise WeatherError(f"기상청 오류: {msg}")

    result = parse_forecast(items, now)
    result["source"] = "기상청"
    _cache[key] = (time.time(), result)
    return result
