"""날씨 — 격자 변환, 발표 시각 계산, 예보 해석, API 오류 처리 (기상청은 호출하지 않는다)"""

from datetime import datetime

from app.services.weather import KST, latest_base_time, latlon_to_grid, parse_forecast, wind_chill


def test_grid_matches_kma_reference():
    assert latlon_to_grid(37.5665, 126.9780) == (60, 127)   # 서울
    assert latlon_to_grid(35.1796, 129.0756) == (98, 76)    # 부산


def test_base_time():
    assert latest_base_time(datetime(2026, 11, 3, 1, 0, tzinfo=KST)) == ("20261102", "2300")
    assert latest_base_time(datetime(2026, 11, 3, 5, 9, tzinfo=KST)) == ("20261103", "0200")
    assert latest_base_time(datetime(2026, 11, 3, 5, 10, tzinfo=KST)) == ("20261103", "0500")


def test_wind_chill_only_when_cold_and_windy():
    assert wind_chill(15, 5) == 15
    assert wind_chill(0, 0.5) == 0
    assert round(wind_chill(0, 5)) == -5


def test_parse_forecast_picks_next_hour():
    now = datetime(2026, 11, 3, 9, 30, tzinfo=KST)
    rows = [("0800", "TMP", "5"), ("0900", "TMP", "7"), ("1000", "TMP", "9"),
            ("0900", "REH", "60"), ("0900", "SKY", "3"), ("0900", "PTY", "0"), ("0900", "WSD", "1")]
    items = [{"fcstDate": "20261103", "fcstTime": t, "category": c, "fcstValue": v} for t, c, v in rows]
    w = parse_forecast(items, now)
    assert w["temp"] == 7 and w["humidity"] == 60 and w["desc"] == "구름많음"


def test_weather_api_without_key_returns_503(client):
    assert client.get("/api/weather?city=서울").status_code == 503
    assert client.get("/api/weather?city=뉴욕").status_code == 404
    assert client.get("/api/weather").status_code == 422
