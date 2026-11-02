"""
seed.py — 바로 둘러볼 수 있는 데모 계정과 옷장을 만든다.

실행: python -m app.seed
계정: demo@moodfit.app / moodfit-demo  (이미 있으면 옷장만 다시 채운다)
"""

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import ClothingItem, User
from app.security import hash_password

DEMO_EMAIL = "demo@moodfit.app"
DEMO_PASSWORD = "moodfit-demo"

ITEMS = [
    ("상의", "사계절", "미니멀", "화이트", "옥스퍼드 셔츠"),
    ("상의", "가을", "캐주얼", "그레이", "맨투맨"),
    ("상의", "여름", "캐주얼", "네이비", "반팔 티셔츠"),
    ("상의", "겨울", "포멀", "베이지", "울 니트"),
    ("하의", "사계절", "포멀", "블랙", "슬랙스"),
    ("하의", "사계절", "캐주얼", "블루", "데님 팬츠"),
    ("하의", "여름", "스포티", "그레이", "트레이닝 쇼츠"),
    ("아우터", "가을", "미니멀", "베이지", "트렌치코트"),
    ("아우터", "겨울", "캐주얼", "블랙", "롱패딩"),
    ("신발", "사계절", "캐주얼", "화이트", "스니커즈"),
    ("신발", "사계절", "포멀", "브라운", "로퍼"),
    ("가방", "사계절", "미니멀", "블랙", "토트백"),
]


def main():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(email=DEMO_EMAIL, nickname="데모", password_hash=hash_password(DEMO_PASSWORD),
                        gender="여성", body_type="보통", skin_tone="밝음", personal_color="여름 쿨톤", height_cm=163)
            db.add(user)
            db.flush()
        for it in list(user.items):
            db.delete(it)
        for category, season, style, color, name in ITEMS:
            db.add(ClothingItem(user_id=user.id, category=category, season=season, style=style, color=color, name=name))
        db.commit()
    print(f"데모 계정 준비 완료: {DEMO_EMAIL} / {DEMO_PASSWORD} (옷 {len(ITEMS)}벌)")


if __name__ == "__main__":
    main()
