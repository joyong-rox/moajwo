from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
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


class GlossaryEntry(Base):
    """용어사전 항목 (기관·자치단체). 사용자가 업로드·등록한다. 처음에는 예시 몇 개만 들어 있다.

    code: 7자리 기관코드 (현행 기관끼리 겹치지 않으므로 이것 하나로 식별)
    sido_code / sigungu_code: 자치단체의 시도 2자리 / 시군구 3자리 코드 (선택, 지역 약칭·지역코드 열 연결에 사용)
    valid_from/valid_to: 존속 기간 (valid_to는 그날까지 포함). previous_codes: 개편 전 항목 코드(쉼표 구분)
    """

    __tablename__ = "glossary_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(7), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    type_label: Mapped[str | None] = mapped_column(String(64))
    sido_code: Mapped[str | None] = mapped_column(String(2))
    sigungu_code: Mapped[str | None] = mapped_column(String(3))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    previous_codes: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(16), default="user")  # example / upload / user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GlossaryAlias(Base):
    """용어사전 별칭. 7자리 코드(target_code)의 항목에 연결한다.

    valid_from/valid_to: 이 별칭이 그 대상을 뜻하는 기간 (비우면 제한 없음, valid_to는 그날까지 포함)
    """

    __tablename__ = "glossary_aliases"
    __table_args__ = (UniqueConstraint("normalized", "target_code", "valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(255))
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    target_code: Mapped[str] = mapped_column(String(7))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(16), default="user")  # example / upload / user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
