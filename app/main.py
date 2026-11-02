"""
main.py — MoodFit FastAPI 앱

실행: uvicorn app.main:app --reload   → http://127.0.0.1:8000  (API 문서: /docs)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import Base, engine
from app.routers import auth, recommend, wardrobe, weather

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC_DIR = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)          # 테이블이 없으면 만든다
    if config.SECRET_KEY == config.DEV_SECRET:
        logging.warning("SECRET_KEY가 개발용 기본값입니다. 배포할 때는 .env에 긴 무작위 값을 넣으세요.")
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="MoodFit API", version="2.0.0", lifespan=lifespan,
              description="기분·날씨·상황과 내 옷장을 바탕으로 오늘의 코디를 추천합니다.")

app.include_router(auth.router)
app.include_router(wardrobe.router)
app.include_router(weather.router)
app.include_router(recommend.router)

config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health", tags=["상태"])
def health():
    return {"status": "ok", "llm": bool(config.GROQ_API_KEY), "weather": bool(config.KMA_API_KEY)}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
