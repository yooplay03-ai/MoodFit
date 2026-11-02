"""
db.py — SQLAlchemy로 DB 연결을 만든다.

기본은 파일 하나로 동작하는 SQLite이고, DATABASE_URL만 바꾸면 PostgreSQL 등으로 옮길 수 있다.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """요청마다 DB 세션을 열고, 응답이 끝나면 닫는다 (FastAPI 의존성)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
