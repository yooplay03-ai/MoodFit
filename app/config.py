"""
config.py — 환경변수로 받는 설정을 한곳에 모은다.

API 키처럼 노출되면 안 되는 값은 코드에 쓰지 않고, 서버를 실행하는 환경(.env 파일 또는 배포 서비스의 Secret)에서만 읽는다.
"""

import os
from pathlib import Path

try:  # .env 파일이 있으면 읽는다 (없어도 동작)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# 외부 API 키 — 없으면 해당 기능만 "설정되지 않음"으로 안내하고 서버는 정상 동작
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")  # llama-3.3-70b-versatile은 2026-08-16 지원 종료
KMA_API_KEY = os.environ.get("KMA_API_KEY", "")  # 공공데이터포털 기상청 단기예보 서비스 키

# 로그인 토큰 서명용 비밀값 — 배포할 때는 반드시 긴 무작위 값으로 바꾼다
DEV_SECRET = "dev-only-secret-change-me-before-deploy"
SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET)
TOKEN_EXPIRE_HOURS = int(os.environ.get("TOKEN_EXPIRE_HOURS", "72"))

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'moodfit.db'}")
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", BASE_DIR / "media"))   # 옷 사진 저장 폴더
PHOTO_CUTOUT = os.environ.get("PHOTO_CUTOUT", "1") == "1"                # 옷 사진 배경 제거 (rembg 필요)
MAX_PHOTO_BYTES = 2 * 1024 * 1024                                   # 사진 1장 최대 2MB (브라우저에서 줄여서 올림)
