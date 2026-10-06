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


class ReviewSession(Base):
    """통합 전 검토 세션. 검증·매핑·자동 보정 결과를 항목으로 펼쳐 두고 사용자의 승인/거부를 기록한다.

    status: in_review / ready(검토 완료, 통합 가능)
    """

    __tablename__ = "review_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="in_review")
    params: Mapped[str] = mapped_column(Text)  # JSON (검증 조건)
    file_ids: Mapped[str] = mapped_column(Text)  # JSON
    excluded_file_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON (개인정보 거부 등으로 제외)
    validation_run_id: Mapped[int | None] = mapped_column(ForeignKey("validation_runs.id", ondelete="SET NULL"))
    remember_format: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base):
    """검토 항목. category: block(차단) / pending(확인 필요) / auto(자동 적용) / warning(검증 경고, 참고)

    reason_source: rule(규칙 템플릿 문장) / llm(LLM이 생성한 문장)
    decision: pending 항목만 사용 — None(대기) / approved / rejected
    """

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("review_sessions.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="SET NULL"))
    file_name: Mapped[str | None] = mapped_column(String(255))
    sheet: Mapped[str | None] = mapped_column(String(255))
    row: Mapped[int | None]
    column: Mapped[str | None] = mapped_column(String(255))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    reason_source: Mapped[str] = mapped_column(String(8), default="rule")
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON (후보, 대상 키 등)
    decision: Mapped[str | None] = mapped_column(String(16))
    choice: Mapped[str | None] = mapped_column(String(64))  # 승인 시 고른 값 (표준 항목 key / 7자리 코드)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
