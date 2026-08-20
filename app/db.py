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
    from sqlalchemy import text

    from app.models.auth import OpsUser  # noqa: F401
    from app.models.job import Base
    from app.models.settings import AppSettingsRow  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # Nautobot uses UUID ids — widen legacy integer columns if present.
    with engine.begin() as conn:
        for col in ("netbox_ip_id", "netbox_vm_id"):
            conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                      IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'provisioning_jobs'
                          AND column_name = '{col}'
                          AND data_type IN ('integer', 'bigint', 'smallint')
                      ) THEN
                        EXECUTE 'ALTER TABLE provisioning_jobs
                                 ALTER COLUMN {col} TYPE VARCHAR(64)
                                 USING {col}::text';
                      END IF;
                    END $$;
                    """
                )
            )

