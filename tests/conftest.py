"""
테스트 공통 준비 — 매 테스트마다 빈 임시 DB와 사진 폴더를 쓰고, 외부 API(Groq, 기상청)는 호출하지 않는다.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'test.db'}"
    os.environ["MEDIA_DIR"] = str(tmp_path / "media")
    os.environ["PHOTO_CUTOUT"] = "0"          # 테스트에서는 배경 제거 모델을 돌리지 않는다
    # 설정을 바꾼 뒤 앱 모듈을 새로 불러와야 임시 DB를 쓴다
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    from fastapi.testclient import TestClient
    from app import config
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "KMA_API_KEY", "")
    from app.main import app
    with TestClient(app) as c:
        yield c


def signup(client, email="user@example.com", password="password123", nickname="유나"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "nickname": nickname})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def add_item(client, headers, **fields):
    data = {"category": "상의", "season": "사계절", "style": "캐주얼", **fields}
    r = client.post("/api/wardrobe", data=data, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()
