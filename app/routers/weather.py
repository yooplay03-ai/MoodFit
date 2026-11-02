"""날씨 — 위치(위도·경도) 또는 도시 이름으로 지금 날씨를 돌려준다. 기상청 키는 서버에만 있다."""

from fastapi import APIRouter, HTTPException, Query, status

from app.services import weather as svc

router = APIRouter(prefix="/api/weather", tags=["날씨"])


@router.get("")
def current_weather(
    lat: float | None = Query(None, ge=33, le=39),     # 기상청 격자는 한반도 주변만 다룬다
    lon: float | None = Query(None, ge=124, le=132),
    city: str | None = Query(None, max_length=10),
):
    if city:
        name = city.strip()
        found = svc.CITIES.get(name) or next((v for k, v in svc.CITIES.items() if name in k), None)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' 도시를 찾을 수 없어요. 예: {', '.join(list(svc.CITIES)[:5])}")
        lat, lon, place = *found, next(k for k, v in svc.CITIES.items() if v == found)
    elif lat is not None and lon is not None:
        place = "현재 위치"
    else:
        raise HTTPException(422, "위치(lat, lon) 또는 도시(city)가 필요합니다.")

    try:
        result = svc.get_weather(lat, lon)
    except svc.WeatherNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "서버에 날씨 API 키가 없어요. 날씨를 직접 입력해 주세요.")
    except svc.WeatherError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"날씨를 불러오지 못했어요. ({e})")
    return {**result, "place": place}


@router.get("/cities")
def cities():
    return list(svc.CITIES)
