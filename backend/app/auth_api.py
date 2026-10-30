"""로그인·계정 API.

- 공개 최초 설정(`POST /auth/setup`)과 공개 회원가입(`POST /auth/signup`)은 종료됐다 (둘 다 410, 본문을 읽지 않는다).
  계정은 설치 담당자의 초기화 명령(`python -m app.adminctl init` — 관리자 아이디 admin, 역할 owner)과 최고 관리자의 계정
  발급으로만 생긴다. 초기화 전(계정 0개·표시 없음)에는 `/auth/status`가 `setup_required: true`를 돌려주고 화면은 "설치
  담당자의 초기화가 필요하다"고만 안내한다.
- 계정 발급(`POST /users`): 최고 관리자가 아이디·표시 이름만 넣으면 서버가 임시 비밀번호를 만들어 해시만 저장하고,
  평문은 그 응답에서 **한 번만** 돌려준다(저장·기록·다시 조회 없음, `Cache-Control: no-store`). 발급된 계정은 임시
  비밀번호로 로그인하면 비밀번호 변경· 본인 조회·로그아웃만 할 수 있고(제한된 로그인 — 업무 API는 403, app/auth.py
  require_full_access), 본인 비밀번호로 바꾼 뒤 다시 로그인해야 업무 기능을 쓴다. 임시 비밀번호는 유효
  시간(config.TEMP_PASSWORD_HOURS, 기본 24시간)이 지나면 로그인에 쓸 수 없다 — 최고 관리자가 재발급한다.
- 비밀번호 초기화(`POST /users/{id}/password`)도 임시 비밀번호 재발급이다: 기존 비밀번호·기존 임시 비밀번호·세션은 그
  즉시 효력을 잃는다(인증 버전). 이용 중지 계정은 재발급으로 활성화되지 않는다. 화면이 본 인증
  버전(expected_auth_version)이 지금 값과 다르면 (다른 재발급·상태 변경이 먼저 반영됨) 덮지 않고 409.
- 계정 관리(예전 공개 가입 신청의 승인·반려, 이용 중지·재개, 발급·재발급)는 최고 관리자만 한다. 모든 변경은
  account_events에 남긴다 (같은 트랜잭션). 비밀번호·해시·쿠키·토큰은 발급 응답 외 어디에도 넣지 않는다.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    COOKIE_SECURE,
    SESSION_HOURS,
    SETUP_KEY,
    STATUS_LABEL,
    AccountStatusError,
    AuthError,
    CurrentUser,
    CurrentUserDep,
    OwnerDep,
    TempPasswordExpired,
    check_password_rule,
    check_username_available,
    check_username_rule,
    conflict,
    current_user,
    end_sessions,
    generate_temp_password,
    hash_password,
    issue_temp_password,
    login,
    logout,
    record_event,
    role_label,
    set_status,
    temp_password_expired,
    temp_password_expiry,
    verify_password,
)
from app.config import TEMP_PASSWORD_HOURS
from app.db import get_session
from app.models import IntegrationRun, ReviewSession, SourceFile, User, ValidationRun
from app.name_limits import OptionalStoredText, StoredText, no_nul
from app.timefmt import utc_to_kst

__all__ = ["SETUP_KEY", "router", "users_router"]

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
# 계정 상태 필터 — auth.STATUSES와 같은 값 (tests/test_nul_inputs.py가 대조). 다른 글자는 422(literal_error)
AccountStatus = Literal["pending", "active", "rejected", "suspended"]
SETUP_CLOSED = (
    "공개 최초 설정은 종료되었습니다. 설치 담당자가 초기화 명령(python -m app.adminctl init)으로 "
    "관리자 계정(admin)을 만듭니다."
)
SIGNUP_CLOSED = "공개 회원가입은 종료되었습니다. 계정은 관리자에게 요청해 주세요."
PROVISION_MESSAGE = (
    "임시 비밀번호는 이 화면에서 한 번만 보입니다. 사용자에게 안전한 방법으로 직접 전하세요. 사용자는 첫 로그인에서 "
    "본인 비밀번호로 바꿔야 업무 기능을 쓸 수 있습니다."
)
_event = record_event


class MeOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str
    must_change_password: bool = (
        False  # 임시 비밀번호 상태 — 화면은 비밀번호 변경만 보인다 (서버도 업무 API를 403으로 막음)
    )
    temp_password_expires_at: datetime | None = None  # 임시 비밀번호 만료(KST)


def me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        role_label=role_label(user.role),
        must_change_password=user.must_change_password,
        temp_password_expires_at=utc_to_kst(user.temp_password_expires_at),
    )


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )


def _no_store(response: Response) -> None:
    """임시 비밀번호가 든 응답은 브라우저·중간 저장소에 남기지 않는다."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


class StatusOut(BaseModel):
    setup_required: bool  # 초기화 전(계정 0개·표시 없음) — 화면은 설치 담당자의 초기화가 필요하다고만 안내한다
    user: MeOut | None = None  # 로그인돼 있으면 그 사용자 (로그인 전 화면이 401 없이 상태를 알 수 있게)


@router.get("/status", response_model=StatusOut)
def auth_status(request: Request, db: Annotated[Session, Depends(get_session)]) -> StatusOut:
    from app.models import AppSetting

    setup_required = (db.scalar(select(func.count(User.id))) or 0) == 0 and db.get(AppSetting, SETUP_KEY) is None
    try:
        row = db.get(User, current_user(request, db).id)
    except HTTPException:
        row = None
    return StatusOut(setup_required=setup_required, user=me_out(row) if row is not None else None)


@router.post("/setup", status_code=status.HTTP_410_GONE)
def setup_closed() -> None:
    """공개 최초 설정 종료 — 본문을 읽지 않고(되돌리지 않고) 410. 관리자 계정은 설치 담당자의 초기화 명령이 만든다."""
    raise HTTPException(status.HTTP_410_GONE, SETUP_CLOSED)


@router.post("/signup", status_code=status.HTTP_410_GONE)
def signup_closed() -> None:
    """공개 회원가입 종료 — 본문을 읽지 않고 410. 계정은 최고 관리자가 발급한다."""
    raise HTTPException(status.HTTP_410_GONE, SIGNUP_CLOSED)


class LoginIn(BaseModel):
    username: StoredText = Field(max_length=64)  # 저장하지 않지만 SELECT 매개변수 — NUL은 두 DB 같은 422로
    password: str = Field(max_length=256)


@router.post("/login", response_model=MeOut)
def do_login(body: LoginIn, response: Response, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    try:
        user, token = login(db, body.username, body.password)
    except AccountStatusError as exc:  # 아이디·비밀번호가 맞을 때만 — 본인 계정의 상태 안내 (세션 없음)
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except TempPasswordExpired as exc:  # 아이디·임시 비밀번호가 맞지만 만료 — 재발급 안내 (세션 없음)
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    _set_cookie(response, token)
    return me_out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def do_logout(request: Request, db: Annotated[Session, Depends(get_session)]) -> Response:
    logout(db, request.cookies.get(COOKIE_NAME))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=MeOut)
def me(user: CurrentUserDep, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    row = db.get(User, user.id)
    assert row is not None
    return me_out(row)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=128)
    new_password_confirm: str | None = Field(default=None, max_length=128)  # 화면은 두 번 입력 — 보내면 같아야 한다


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeIn, user: CurrentUserDep, db: Annotated[Session, Depends(get_session)]
) -> Response:
    """본인 비밀번호 변경. 임시 비밀번호 상태(발급 직후)에서는 이것이 유일한 쓰기 — 바꾸면 임시 상태가 끝나고 세션이
    모두
    끝나므로 새 비밀번호로 다시 로그인한다 (일반 변경은 지금 화면의 세션만 새로 잇는다)."""
    row = db.get(User, user.id)
    assert row is not None
    version, first = row.auth_version, row.must_change_password
    if not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status.HTTP_409_CONFLICT, "현재 비밀번호가 올바르지 않습니다.")
    if first and temp_password_expired(row):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "임시 비밀번호의 유효 기간이 지났습니다. 관리자에게 재발급을 요청하세요."
        )
    try:
        check_password_rule(body.new_password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.new_password_confirm is not None and body.new_password_confirm != body.new_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "새 비밀번호 확인이 일치하지 않습니다.")
    if body.new_password == body.current_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "새 비밀번호는 지금 비밀번호" + ("(임시 비밀번호)" if first else "") + "와 달라야 합니다.",
        )
    # 확인한 그대로일 때만 바꾼다 (그 사이 재발급·중지가 커밋됐으면 0행 — 같은 임시 비밀번호로 두 번 바꾸지 못한다).
    # 버전을 올려 다른 세션·진행 중 로그인·기존 임시 비밀번호를 무효로
    changed = db.execute(
        update(User)
        .where(User.id == row.id, User.auth_version == version, User.must_change_password.is_(first))
        .values(
            password_hash=hash_password(body.new_password),
            auth_version=User.auth_version + 1,
            must_change_password=False,
            temp_password_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "계정 정보가 바뀌었습니다. 다시 로그인한 뒤 시도하세요.")
    end_sessions(db, row.id)
    if first:
        _event(db, None, row, "first_password_change", row.status, row.status,
               reason="임시 비밀번호 → 본인 비밀번호 (제한된 로그인 종료 · 다시 로그인)", actor_self=True)  # fmt: skip
    db.commit()
    db.refresh(row)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    if first:  # 임시 자격·제한 세션은 끝났다 — 새 비밀번호로 다시 로그인한다 (자동으로 잇지 않음)
        out.delete_cookie(COOKIE_NAME, path="/")
        return out
    # 다른 기기의 로그인은 끊고, 지금 화면은 새 세션으로 이어간다
    try:
        _, token = login(db, row.username, body.new_password)
    except AuthError as exc:  # 그 사이 이용 중지·초기화가 끼어든 경우: 변경은 반영됐고 새 세션만 만들지 않는다
        raise HTTPException(
            status.HTTP_409_CONFLICT, "비밀번호는 바뀌었지만 계정 상태가 바뀌어 다시 로그인해야 합니다."
        ) from exc
    _set_cookie(out, token)
    return out


# ---------------------------------------------------------------- 계정 관리 (최고 관리자)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str
    status: str
    status_label: str
    status_reason: str | None
    status_changed_at: datetime | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    last_activity_at: datetime | None  # 실제 업무 활동(업로드·검증·검토·통합 실행)의 마지막 시각 — 로그인과 별개
    must_change_password: bool  # 임시 비밀번호 상태 (첫 로그인에서 본인 비밀번호로 바꿔야 함)
    temp_password_expires_at: datetime | None  # 임시 비밀번호 만료(KST)
    temp_password_expired: bool  # 임시 상태인데 만료 시각이 지남 — 재발급 필요
    auth_version: int  # 화면이 재발급 요청에 되돌려 보내는 값 (그 사이 바뀌었으면 덮지 않음) — 화면에 표시하지 않는다


def user_out(u: User, activity: dict[int, datetime] | None = None) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        role=u.role,
        role_label=role_label(u.role),
        status=u.status,
        status_label=STATUS_LABEL.get(u.status, u.status),
        status_reason=u.status_reason,
        status_changed_at=utc_to_kst(u.status_changed_at),
        is_active=u.is_active,
        created_at=utc_to_kst(u.created_at),
        last_login_at=utc_to_kst(u.last_login_at),
        last_activity_at=utc_to_kst((activity or {}).get(u.id)),
        must_change_password=u.must_change_password,
        temp_password_expires_at=utc_to_kst(u.temp_password_expires_at),
        temp_password_expired=temp_password_expired(u),
        auth_version=u.auth_version,
    )


def last_activity(db: Session) -> dict[int, datetime]:
    """사용자별 마지막 업무 활동 시각 (UTC로 비교). 로그인은 넣지 않는다."""
    out: dict[int, datetime] = {}
    sources = (
        (SourceFile.uploaded_by, SourceFile.uploaded_at),
        (ValidationRun.created_by, ValidationRun.created_at),
        (ReviewSession.created_by, ReviewSession.created_at),
        (IntegrationRun.user_id, IntegrationRun.created_at),
    )
    for owner, at in sources:
        for uid, latest in db.execute(select(owner, func.max(at)).where(owner.is_not(None)).group_by(owner)):
            if latest is None:
                continue
            if isinstance(latest, str):  # SQLite 집계 결과는 문자열로 올 수 있다
                latest = datetime.fromisoformat(latest)
            value = latest if latest.tzinfo is not None else latest.replace(tzinfo=UTC)
            if uid not in out or value > out[uid]:
                out[uid] = value
    return out


@users_router.get("", response_model=list[UserOut])
def list_users(
    _: OwnerDep,
    db: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=64), AfterValidator(no_nul)] = None,
    status_filter: Annotated[AccountStatus | None, Query(alias="status")] = None,
) -> list[UserOut]:
    """아이디·표시 이름 검색(q, 부분 일치), 상태 필터(status). 승인 대기(예전 공개 가입 신청) → 최근 순.
    검색어의 NUL·모르는 상태는 422 — LIKE·= 매개변수를 PostgreSQL 드라이버가 거절하므로 두 DB 같게 요청에서 막는다."""
    stmt = select(User)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(User.username).like(like) | func.lower(User.display_name).like(like))
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    rows = sorted(db.scalars(stmt), key=lambda u: (u.status != "pending", -u.id))
    activity = last_activity(db)
    return [user_out(u, activity) for u in rows]


class ProvisionIn(BaseModel):
    """계정 발급: 아이디·표시 이름만 받는다. 비밀번호·역할·상태는 서버가 정한다 (보내면 422) — 항상 일반 사용자·이용 중·
    임시 비밀번호."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=32)
    display_name: StoredText = Field(min_length=1, max_length=64)


class ProvisionOut(BaseModel):
    user: UserOut
    temp_password: str  # 이 응답에서만 — 서버는 해시만 저장하고 다시 보여 주지 않는다 (잃으면 재발급)
    expires_at: datetime  # 임시 비밀번호 만료(KST)
    valid_hours: int
    message: str


def _provision_out(user: User, plain: str, expires: datetime) -> ProvisionOut:
    return ProvisionOut(
        user=user_out(user),
        temp_password=plain,
        expires_at=utc_to_kst(expires),
        valid_hours=TEMP_PASSWORD_HOURS,
        message=PROVISION_MESSAGE,
    )


@users_router.post("", response_model=ProvisionOut, status_code=status.HTTP_201_CREATED)
def provision_user(
    body: ProvisionIn, actor: OwnerDep, response: Response, db: Annotated[Session, Depends(get_session)]
) -> ProvisionOut:
    """사용자 계정 발급 — 서버가 만든 임시 비밀번호(해시만 저장)를 이 응답에서 한 번 돌려준다.
    아이디 중복은 DB 유일 조건으로도 막는다 (같은 발급을 두 번 누르면 둘째는 409 — 첫 발급을 조용히 덮지 않는다)."""
    username = body.username.strip().lower()
    display_name = body.display_name.strip()
    try:
        check_username_rule(username)
        check_username_available(username)
        if not display_name:
            raise AuthError("표시 이름을 입력하세요.")
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    taken = HTTPException(status.HTTP_409_CONFLICT, f"이미 있는 아이디입니다: {username}")
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise taken
    plain, expires = generate_temp_password(), temp_password_expiry()
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(plain),
        role="user",
        failed_logins=0,
        must_change_password=True,
        temp_password_expires_at=expires,
    )
    set_status(user, "active")
    try:
        db.add(user)
        db.flush()
        _event(db, actor, user, "provision", None, "active", after_role="user",
               reason=f"계정 발급 — 임시 비밀번호(유효 {TEMP_PASSWORD_HOURS}시간), 첫 로그인에서 본인 비밀번호로 변경",
               )  # fmt: skip
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if not conflict(exc):
            raise  # DB에 닿지 못함 → 503 (main) — '이미 있는 아이디'로 보이지 않게
        raise taken from exc
    db.refresh(user)
    _no_store(response)
    return _provision_out(user, plain, expires)


class UserPatch(BaseModel):
    """표시 이름만 바꾼다. 역할·상태는 이 API로 바꾸지 않는다 (상태는 승인·반려·중지·재개 API로)."""

    model_config = ConfigDict(extra="forbid")

    display_name: StoredText = Field(min_length=1, max_length=64)


def _target(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "계정을 찾을 수 없습니다.")
    return user


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatch, _: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> UserOut:
    user = _target(db, user_id)
    user.display_name = body.display_name.strip()
    db.commit()
    return user_out(user, last_activity(db))


# 상태 전이: 동작 → (허용하는 현재 상태, 바뀔 상태). 반려 계정의 승인·중지 계정의 승인처럼 표에 없는 전이는 막는다.
# 승인·반려는 예전 공개 가입으로 남은 승인 대기 계정의 처리다 (새 공개 가입은 받지 않는다).
TRANSITIONS: dict[str, tuple[str, str]] = {
    "approve": ("pending", "active"),
    "reject": ("pending", "rejected"),
    "suspend": ("active", "suspended"),
    "resume": ("suspended", "active"),
}
ACTION_LABEL = {"approve": "가입 승인", "reject": "가입 반려", "suspend": "이용 중지", "resume": "이용 재개"}


class StatusChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: OptionalStoredText = Field(default=None, max_length=500)  # users.status_reason·account_events.reason


def _deny(db: Session, actor: CurrentUser, user: User, action: str, message: str) -> HTTPException:
    """반영하지 않은 요청도 이력에 남긴다 (중복 클릭·오래된 화면에서 보낸 요청 확인용). 계정은 바꾸지 않는다."""
    db.rollback()
    db.refresh(user)
    _event(db, actor, user, action, user.status, user.status, reason=message, result="denied")
    db.commit()
    return HTTPException(status.HTTP_409_CONFLICT, message)


def _change_status(db: Session, actor: CurrentUser, user_id: int, action: str, reason: str | None) -> UserOut:
    user = _target(db, user_id)
    expected, after = TRANSITIONS[action]
    label = ACTION_LABEL[action]
    if user.id == actor.id or user.role == "owner":
        raise _deny(db, actor, user, action, "최고 관리자 계정의 상태는 바꿀 수 없습니다.")
    reason = reason.strip() if reason and reason.strip() else None
    # 조건부 갱신: 그 사이 다른 요청이 상태를 바꿨으면 0행 → 반영하지 않음 (중복·동시 요청에도 한 번만 반영)
    changed = db.execute(
        update(User)
        .where(User.id == user.id, User.status == expected)
        .values(
            status=after,
            is_active=after == "active",
            status_reason=reason,
            status_changed_at=datetime.now(UTC),
            # 반려·이용 중지: 버전을 올려 그 전에 만든 쿠키·진행 중 로그인이 재개 후에도 되살아나지 않게
            auth_version=User.auth_version + (0 if after == "active" else 1),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        db.refresh(user)
        current = STATUS_LABEL.get(user.status, user.status)
        raise _deny(
            db, actor, user, action,
            f"{label}할 수 없습니다: 지금 상태가 '{current}'입니다 ('{STATUS_LABEL[expected]}' 상태에서만 가능).",
        )  # fmt: skip
    if after != "active":
        end_sessions(db, user.id)  # 반려·이용 중지: 남아 있는 쿠키로도 업무 API를 못 쓰게
    _event(db, actor, user, action, expected, after, reason=reason)
    db.commit()
    db.refresh(user)
    return user_out(user, last_activity(db))


@users_router.post("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int, actor: OwnerDep, db: Annotated[Session, Depends(get_session)], body: StatusChangeIn | None = None
) -> UserOut:
    return _change_status(db, actor, user_id, "approve", body.reason if body else None)


@users_router.post("/{user_id}/reject", response_model=UserOut)
def reject_user(
    user_id: int, actor: OwnerDep, db: Annotated[Session, Depends(get_session)], body: StatusChangeIn | None = None
) -> UserOut:
    return _change_status(db, actor, user_id, "reject", body.reason if body else None)


@users_router.post("/{user_id}/suspend", response_model=UserOut)
def suspend_user(
    user_id: int, actor: OwnerDep, db: Annotated[Session, Depends(get_session)], body: StatusChangeIn | None = None
) -> UserOut:
    return _change_status(db, actor, user_id, "suspend", body.reason if body else None)


@users_router.post("/{user_id}/resume", response_model=UserOut)
def resume_user(
    user_id: int, actor: OwnerDep, db: Annotated[Session, Depends(get_session)], body: StatusChangeIn | None = None
) -> UserOut:
    return _change_status(db, actor, user_id, "resume", body.reason if body else None)


class ReissueIn(BaseModel):
    """임시 비밀번호 재발급: 비밀번호를 받지 않는다 (서버가 만든다). expected_auth_version은 화면(목록)이 본 인증 버전 —
    **필수**(0 이상의 정수). 지금 값과 다르면(다른 재발급·상태 변경이 먼저 반영됨) 덮지 않고 409. 본문·버전이 없거나
    정수가 아니면(불리언·문자열·실수 포함) 422 — 조건 없는 재발급 경로는 없다."""

    model_config = ConfigDict(extra="forbid")

    expected_auth_version: StrictInt = Field(ge=0)


@users_router.post("/{user_id}/password", response_model=ProvisionOut)
def reissue_password(
    user_id: int,
    actor: OwnerDep,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
    body: ReissueIn,
) -> ProvisionOut:
    """새 임시 비밀번호를 발급하고(해시만 저장) 그 사용자의 기존 비밀번호·기존 임시 비밀번호·세션을 그 즉시 무효로 한다.
    상태는 바꾸지 않는다 — 이용 중지 계정은 재발급으로 활성화되지 않는다(재개는 따로). 평문은 이 응답에서만.
    화면이 본 인증 버전(필수)과 지금 값이 같을 때만 바꾼다 — 성공 한 번 = 버전 +1·기존 세션 종료·새 임시 비밀번호 저장·
    성공 이력 1건. 다르면 409(거절 이력만 남고 계정·세션은 그대로)."""
    user = _target(db, user_id)
    if user.role == "owner":
        raise _deny(db, actor, user, "password_reset",
                    "최고 관리자 비밀번호는 '내 계정'에서 직접 바꾸거나, "
                    "설치 담당자의 복구 명령(app.adminctl recover)으로 재설정합니다.",
                    )  # fmt: skip
    if user.status == "rejected":
        raise _deny(db, actor, user, "password_reset", "가입 반려된 계정에는 임시 비밀번호를 발급하지 않습니다.")
    issued = issue_temp_password(
        db, actor, user, action="password_reset", expected_version=body.expected_auth_version,
        reason="임시 비밀번호 재발급 — 기존 비밀번호·세션 종료"
        + (" (이용 중지 상태 유지)" if user.status == "suspended" else ""),
    )  # fmt: skip
    if issued is None:
        raise _deny(db, actor, user, "password_reset",
                    "계정 정보가 그 사이 바뀌었습니다 (다른 재발급·상태 변경이 먼저 반영됨). "
                    "목록을 새로 고친 뒤 필요하면 다시 재발급하세요.",
                    )  # fmt: skip
    db.commit()
    db.refresh(user)
    _no_store(response)
    return _provision_out(user, *issued)
