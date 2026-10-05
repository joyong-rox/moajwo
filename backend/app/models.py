from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SourceFile(Base):
    """업로드된 원본 파일 메타데이터. 원본 파일 자체는 읽기 전용으로 보관한다."""

    __tablename__ = "source_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 개인정보 탐지 요약(JSON: 건수 + 위치 + 마스킹 값). 원문은 저장하지 않는다.
    pii_summary: Mapped[str | None] = mapped_column(Text)
    pii_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MappingDecision(Base):
    """사용자가 확정한 열 매핑. field_key가 None이면 '해당 없음'으로 확정한 것."""

    __tablename__ = "mapping_decisions"
    __table_args__ = (UniqueConstraint("source_file_id", "sheet_name", "column_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(255))
    column_name: Mapped[str] = mapped_column(String(255))
    field_key: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LearnedAlias(Base):
    """사용자 확정에서 학습한 별칭. 표준 사전 파일은 건드리지 않고 DB에만 쌓는다."""

    __tablename__ = "learned_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized: Mapped[str] = mapped_column(String(255), unique=True)  # mapping.normalize 결과
    alias: Mapped[str] = mapped_column(String(255))  # 사용자가 본 원래 열 이름
    field_key: Mapped[str] = mapped_column(String(64), index=True)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationRun(Base):
    """통합 전 검증 실행 기록. status: blocked / pii_confirmation_required / passed_with_warnings / passed"""

    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    params: Mapped[str] = mapped_column(Text)  # JSON
    result: Mapped[str] = mapped_column(Text)  # JSON (차단/경고/개인정보/참고 분리)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
