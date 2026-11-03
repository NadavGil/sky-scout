from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Airport(Base):
    __tablename__ = "airports"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    airport_type: Mapped[str | None] = mapped_column(String(64))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    iso_country: Mapped[str | None] = mapped_column(String(8), index=True)
    iso_region: Mapped[str | None] = mapped_column(String(16))
    municipality: Mapped[str | None] = mapped_column(String(255))
    scheduled_service: Mapped[str | None] = mapped_column(String(8))

    identifiers: Mapped[list["AirportIdentifier"]] = relationship(
        back_populates="airport",
        cascade="all, delete-orphan",
    )


class AirportIdentifier(Base):
    __tablename__ = "airport_identifiers"
    __table_args__ = (UniqueConstraint("airport_id", "scheme", "value", name="uq_airport_scheme_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    scheme: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(Text)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)

    airport: Mapped[Airport] = relationship(back_populates="identifiers")


class DatasetSync(Base):
    __tablename__ = "dataset_syncs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int]
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class RawSnapshot(Base):
    __tablename__ = "raw_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int | None] = mapped_column(ForeignKey("airports.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    request_params: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    airport_id: Mapped[int] = mapped_column(ForeignKey("airports.id"), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"), nullable=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    value_kind: Mapped[str] = mapped_column(String(32))
    quality_flags: Mapped[str] = mapped_column(Text, default="[]")
    coverage_note: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class AnalystEvidence(Base):
    __tablename__ = "analyst_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    airport_id: Mapped[int | None] = mapped_column(ForeignKey("airports.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    value_text: Mapped[str] = mapped_column(Text)
    value_kind: Mapped[str] = mapped_column(String(32))
    supplied_at: Mapped[datetime] = mapped_column(DateTime, index=True)
