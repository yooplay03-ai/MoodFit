from conftest import signup


def test_signup_login_and_me(client):
    headers = signup(client)
    me = client.get("/api/me", headers=headers).json()
    assert me["email"] == "user@example.com" and me["nickname"] == "유나"

    r = client.post("/api/auth/login", json={"email": "USER@example.com", "password": "password123"})
    assert r.status_code == 200          # 이메일 대소문자는 구분하지 않는다
    assert "token" in r.json()


def test_password_is_not_stored_in_plain_text(client):
    signup(client, password="password123")
    from app.db import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        stored = db.query(User).one().password_hash
    assert "password123" not in stored and stored.startswith("pbkdf2_sha256$")


def test_wrong_password_and_unknown_email_get_same_answer(client):
    signup(client)
    wrong = client.post("/api/auth/login", json={"email": "user@example.com", "password": "nope-nope"})
    unknown = client.post("/api/auth/login", json={"email": "who@example.com", "password": "nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_duplicate_email_and_short_password(client):
    signup(client)
    dup = client.post("/api/auth/signup", json={"email": "user@example.com", "password": "password123", "nickname": "a"})
    assert dup.status_code == 409
    short = client.post("/api/auth/signup", json={"email": "b@example.com", "password": "short", "nickname": "b"})
    assert short.status_code == 422


def test_protected_api_needs_valid_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_update_profile(client):
    headers = signup(client)
    r = client.put("/api/me/profile", headers=headers,
                   json={"gender": "여성", "body_type": "슬림", "skin_tone": "밝음", "personal_color": "여름 쿨톤", "height_cm": 163})
    assert r.status_code == 200 and r.json()["personal_color"] == "여름 쿨톤"
    bad = client.put("/api/me/profile", headers=headers, json={"personal_color": "보라색"})
    assert bad.status_code == 422
