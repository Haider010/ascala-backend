import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.sqlalchemy import Base


class EscouadeBatch(Base):
    __tablename__ = "escouade_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    member_type: Mapped[str] = mapped_column(String(40), nullable=False)
    batch_name: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(80))
    source_label: Mapped[str | None] = mapped_column(Text)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    strategy_review: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    quality_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list["EscouadeItem"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="EscouadeItem.created_at.asc()",
    )


class EscouadeItem(Base):
    __tablename__ = "escouade_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "post_id", name="uq_escouade_items_batch_post_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("escouade_batches.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    location_id: Mapped[str] = mapped_column(Text, index=True, nullable=False)
    post_id: Mapped[str] = mapped_column(String(80), nullable=False)
    member_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    batch: Mapped[EscouadeBatch] = relationship(back_populates="items")
