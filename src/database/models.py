"""SQLAlchemy database models."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CertificateValidation(Base):
    """Stores the validation result of a German Führungszeugnis."""

    __tablename__ = "certificate_validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    paperless_document_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, unique=True
    )

    person_name: Mapped[str | None] = mapped_column(String(255))
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancellation_date: Mapped[date | None] = mapped_column(Date)
    last_expiry_alert_at: Mapped[date | None] = mapped_column(Date)
    stamm_name: Mapped[str | None] = mapped_column(String(255))
    dioezese_name: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<CertificateValidation id={self.id} "
            f"doc={self.paperless_document_id} valid={self.is_valid}>"
        )
