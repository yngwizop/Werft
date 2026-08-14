from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.job import Base


class AppSettingsRow(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
