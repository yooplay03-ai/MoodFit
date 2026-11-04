"""
db.py — SQLAlchemy로 DB 연결을 만든다.

기본은 파일 하나로 동작하는 SQLite이고, DATABASE_URL만 바꾸면 PostgreSQL 등으로 옮길 수 있다.
"""

from sqlalchemy import create_engine, inspect, text
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


# 나중에 추가한 열 — 이미 만든 DB 파일에도 넣어 준다 (create_all은 있는 테이블을 바꾸지 않는다)
ADDED_COLUMNS = {"clothing_items": {"season_by_user": "BOOLEAN NOT NULL DEFAULT 0"}}


def add_missing_columns():
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
