import os
from datetime import datetime, timezone
from sqlmodel import Session, create_engine

RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://admin:admin@postgres:5432/validator_db",
)

if RAW_DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = RAW_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    DATABASE_URL = RAW_DATABASE_URL

engine = create_engine(DATABASE_URL)


def get_session():
    with Session(engine) as session:
        yield session


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)