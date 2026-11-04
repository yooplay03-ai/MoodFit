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


def test_weather_api_validates_input(client):
    assert client.get("/api/weather?city=뉴욕").status_code == 404
    assert client.get("/api/weather").status_code == 422


def test_encoded_key_is_decoded_and_xml_error_is_explained(monkeypatch):
    """포털의 'Encoding' 키를 넣어도 두 번 인코딩되지 않고, XML 오류는 원인을 알려 준다."""
    import httpx
    from app import config
    from app.services import weather as svc
    monkeypatch.setattr(config, "KMA_API_KEY", "abc%2Bdef%3D%3D")
    sent = {}

    def fake_get(url, params, timeout):
        sent.update(params)
        xml = ("<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR"
               "</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")
        return httpx.Response(200, text=xml)
    monkeypatch.setattr(svc.httpx, "get", fake_get)
    svc._cache.clear()
    try:
        svc.get_weather(37.5665, 126.9780)
        assert False, "오류가 나야 한다"
    except svc.WeatherError as e:
        assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in str(e) and "활용신청" in str(e)
    assert sent["serviceKey"] == "abc+def=="


def test_without_kma_key_uses_open_meteo(client, monkeypatch):
    """기상청 키가 없으면 키 없는 Open-Meteo로 날씨를 가져온다."""
    import httpx
    from app.services import weather as svc
    data = {"current": {"time": "2026-11-04T21:15", "temperature_2m": 11.6, "relative_humidity_2m": 62,
                        "apparent_temperature": 9.4, "weather_code": 61},
            "hourly": {"time": ["2026-11-04T20:00", "2026-11-04T21:00"], "precipitation_probability": [40, 70]}}
    called = {}

    def fake_get(url, params, timeout):
        called["url"] = url
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))
    monkeypatch.setattr(svc.httpx, "get", fake_get)
    svc._cache.clear()
    r = client.get("/api/weather", params={"city": "서울"})
    assert r.status_code == 200
    body = r.json()
    assert called["url"] == svc.OPEN_METEO_URL
    assert body == {"temp": 12, "desc": "비", "humidity": 62, "feels_like": 9, "rain_prob": 70,
                    "source": "Open-Meteo", "place": "서울"}
