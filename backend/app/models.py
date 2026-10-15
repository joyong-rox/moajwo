from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
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
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    data_rows: Mapped[int | None]  # 데이터 행 수 (대시보드 '검증한 행 수' 집계용)


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
    """사용자 확정에서 학습한 별칭 (개인별). 표준 사전 파일은 건드리지 않고 DB에만 쌓는다.

    user_id: 학습한 사용자. 계정 도입 전에 학습한 별칭은 None(모든 사용자에게 적용).
    여러 사용자가 같은 매핑을 승인하면 관리자 심사를 거쳐 전사 표준 사전(standard_terms)으로 승격된다.
    """

    __tablename__ = "learned_aliases"
    __table_args__ = (UniqueConstraint("normalized", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    normalized: Mapped[str] = mapped_column(String(255))  # mapping.normalize 결과
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
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
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
    source: Mapped[str] = mapped_column(String(16), default="user")  # example / upload / user / base(개편 승계)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewSession(Base):
    """통합 전 검토 세션. 검증·매핑·자동 보정 결과를 항목으로 펼쳐 두고 사용자의 승인/거부를 기록한다.

    status: in_review / ready(검토 완료, 통합 가능) / superseded(다시 검사로 대체됨)
    parent_id: 다시 검사로 만든 세션이면 이전 세션
    """

    __tablename__ = "review_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("review_sessions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="in_review")
    params: Mapped[str] = mapped_column(Text)  # JSON (검증 조건)
    file_ids: Mapped[str] = mapped_column(Text)  # JSON
    excluded_file_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON (개인정보 거부 등으로 제외)
    validation_run_id: Mapped[int | None] = mapped_column(ForeignKey("validation_runs.id", ondelete="SET NULL"))
    remember_format: Mapped[bool] = mapped_column(default=False)
    automation_id: Mapped[int | None] = mapped_column(ForeignKey("automations.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
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


class IntegrationRun(Base):
    """실행 기록 (runs). 검토가 끝난 세션으로 새 결과 파일을 만든다 (원본은 덮어쓰지 않음).

    status: review(검토 대기 — 자동화 실행에서 확인할 항목이 남음) / running / done(완료) / failed(실패)
    trigger: manual(검토 패널에서 직접) / automation(저장된 자동화로 실행)
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_session_id: Mapped[int | None] = mapped_column(ForeignKey("review_sessions.id", ondelete="SET NULL"))
    automation_id: Mapped[int | None] = mapped_column(ForeignKey("automations.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16))
    input_file_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_path: Mapped[str | None] = mapped_column(String(1024))  # DATA_DIR 기준 상대 경로
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    file_ids: Mapped[str] = mapped_column(Text)  # JSON (통합에 포함한 파일)
    row_count: Mapped[int] = mapped_column(default=0)
    change_count: Mapped[int] = mapped_column(default=0)
    warning_count: Mapped[int] = mapped_column(default=0)
    summary: Mapped[str] = mapped_column(Text, default="{}")  # JSON (처리 요약)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunChange(Base):
    """통합 실행에서 바뀐 값 하나. 통합 결과에서 원본과 달라진 셀(그리고 열 이름 매핑)은 모두 여기에 기록한다.

    output_row: 결과 파일 '통합 데이터' 시트의 행 번호 (열 이름 변경은 헤더 행 1)
    kind: ffill(병합 셀 채움) / number(숫자 변환) / trim(공백 정리) / entity(이름 정규화) / column(열 이름 매핑) / other
    """

    __tablename__ = "run_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey("source_files.id", ondelete="SET NULL"))
    file_name: Mapped[str] = mapped_column(String(255))
    sheet: Mapped[str] = mapped_column(String(255))
    source_row: Mapped[int | None]
    output_row: Mapped[int]
    column: Mapped[str] = mapped_column(String(255))  # 결과 파일의 열 이름
    source_column: Mapped[str] = mapped_column(String(255))  # 원본 파일의 열 이름
    kind: Mapped[str] = mapped_column(String(16))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)


class Automation(Base):
    """저장된 자동화: 승인된 매핑 규칙·검증 조건·출력 형식 (입력 파일은 저장하지 않음).

    column_rules: [{column, normalized, field_key(None=해당 없음), field_label}] — 열 이름 기준
    entity_rules: {이름: 7자리 코드} — 검토에서 승인한 기관·지역 이름 연결
    validation: {required_entities, entity_field, change_threshold, expected_period(지난 실행 값, 기본값으로만 사용)}
    output_format: {columns: [결과 열 순서]}
    """

    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # 이 자동화를 사람이 직접 할 때 걸리는 시간(분). 비우면 설정의 기본값을 쓴다 (절약 시간 계산)
    manual_baseline_minutes: Mapped[float | None] = mapped_column(Float)
    source_run_id: Mapped[int | None]  # 저장한 실행 번호 (기록용, 외래키 순환을 피하려고 제약 없음)
    column_rules: Mapped[str] = mapped_column(Text)
    entity_rules: Mapped[str] = mapped_column(Text)
    validation: Mapped[str] = mapped_column(Text)
    output_format: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """사용자 계정. role: owner(최고 관리자·소유자) / user(일반 사용자)

    - user: 자기 파일·검토·실행 기록·자동화만 다룬다.
    - owner: 전체 기록 조회(읽기 전용), 가입 승인·사용자 관리, 이용 현황, 설정, 공통 사전·승격 심사.
    어떤 역할도 통합 결과 데이터(결과 파일·변경 기록)를 직접 수정할 수 없다.
    (예전 중간 관리자 역할 admin은 0016에서 user로 바뀌었다.)

    status(계정 상태, 역할과 별개): pending(승인 대기) / active(이용 중) / rejected(가입 반려) / suspended(이용 중지).
    업무 기능은 active만 쓴다. is_active는 status == 'active'와 항상 같게 유지한다 (이전 코드와의 호환·이중 확인).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(500))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 인증 버전: 비밀번호 변경·초기화, 가입 반려·이용 중지 때 1씩 올린다. 세션은 발급 시점의 버전과 같을 때만 유효하다
    auth_version: Mapped[int] = mapped_column(default=0)
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MappingAnalysis(Base):
    """사용자가 마지막으로 실행한 파일의 열 매핑 분석 결과 (파일·사용자마다 1건, 다시 분석하면 바꿔 씀).

    화면으로 내보낸 결과(열 이름·샘플·근거는 이미 개인정보 마스킹)를 그대로 저장한다. 원본 값은 저장하지 않는다.
    여러 파일 공통 검토는 이 기록을 모아 보므로, 화면을 열 때 다시 분석하거나 모델을 호출하지 않는다.
    """

    __tablename__ = "mapping_analyses"
    __table_args__ = (UniqueConstraint("source_file_id", "created_by"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    result: Mapped[str] = mapped_column(Text)  # FileMappingOut JSON (마스킹된 값)


class AccountEvent(Base):
    """계정 관리 이력: 가입 신청·승인·반려·이용 중지·재개·비밀번호 초기화·계정 추가·역할 전환.

    작업자·대상은 이름도 함께 남겨 계정이 바뀌어도 읽을 수 있게 한다. 비밀번호·쿠키·토큰은 남기지 않는다.
    result: ok(반영됨) / denied(상태 전이가 맞지 않아 반영하지 않음)
    """

    __tablename__ = "account_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    target_name: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32))
    before_status: Mapped[str | None] = mapped_column(String(16))
    after_status: Mapped[str | None] = mapped_column(String(16))
    before_role: Mapped[str | None] = mapped_column(String(16))
    after_role: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    result: Mapped[str] = mapped_column(String(16), default="ok")


class AuthSession(Base):
    """로그인 세션. 브라우저 쿠키에는 임의 토큰을, DB에는 그 해시만 저장한다."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    auth_version: Mapped[int] = mapped_column(default=0)  # 발급 시점의 users.auth_version (다르면 무효)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MappingApproval(Base):
    """사용자가 승인한 열 매핑 (열 이름 → 표준 항목).

    같은 매핑을 서로 다른 사용자 3명 이상이 승인하면 승격 후보가 된다.
    """

    __tablename__ = "mapping_approvals"
    __table_args__ = (UniqueConstraint("normalized", "field_key", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    column_name: Mapped[str] = mapped_column(String(255))  # 승인할 때 본 열 이름 (예시)
    field_key: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromotionCandidate(Base):
    """전사 표준 사전 승격 심사 대기열. status: pending(심사 대기) / approved(반영됨) / rejected(반려)"""

    __tablename__ = "promotion_candidates"
    __table_args__ = (UniqueConstraint("normalized", "field_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized: Mapped[str] = mapped_column(String(255))
    column_name: Mapped[str] = mapped_column(String(255))
    field_key: Mapped[str] = mapped_column(String(64))
    approver_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(500))


class StandardTerm(Base):
    """전사 표준 사전: 관리자 승인으로 승격된 열 이름 → 표준 항목. 모든 사용자의 1단계(정확 일치)에 적용된다.

    기본 표준 사전 파일(standard_dictionary.json)은 바꾸지 않고, 겹치면 기본 사전이 우선한다.
    """

    __tablename__ = "standard_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized: Mapped[str] = mapped_column(String(255), unique=True)
    term: Mapped[str] = mapped_column(String(255))
    field_key: Mapped[str] = mapped_column(String(64))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("promotion_candidates.id", ondelete="SET NULL"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    """소유자가 정하는 설정 (key → JSON 값). 예: manual_baseline_minutes(실행 1건을 사람이 할 때 걸리는 시간, 분)"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisRun(Base):
    """확정된 열로 실행한 데이터 분석 결과 (1차). 실행한 사람이 보고, 최고 관리자는 조회만 한다.

    계산 조건·사용한 파일 해시·시트·열·그 시트의 확정 매핑·제외 정책·결과를 함께 저장한다. 결과 화면·페이지·Excel은
    이 저장값으로 만들므로, 나중에 매핑이 바뀌어도 과거 결과가 조용히 바뀌지 않는다.
    통합 실행(runs)·절약 시간 통계와 별개다.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    field_key: Mapped[str] = mapped_column(String(64))
    partial: Mapped[bool] = mapped_column(default=False)  # 자료 문제(빈칸·비공개 등)로 뺀 값이 있음
    summary: Mapped[str] = mapped_column(Text)  # 목록용 JSON
    conditions: Mapped[str] = mapped_column(Text)  # 계산 조건 JSON
    inputs: Mapped[str] = mapped_column(Text)  # 사용한 파일·시트·열·확정 매핑·sha256 JSON (이름은 가림)
    result: Mapped[str] = mapped_column(Text)  # 결과 JSON (그룹 결과·제외 사유·근거 행 목록)
