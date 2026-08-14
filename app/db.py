from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_infra

infra = get_infra()
engine = create_engine(infra.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models.auth import OpsUser  # noqa: F401
    from app.models.job import Base
    from app.models.settings import AppSettingsRow  # noqa: F401

    Base.metadata.create_all(bind=engine)
