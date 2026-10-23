"""로그인·계정 API.

- 처음 실행(계정 0개)에는 최초 소유자(= 최고 관리자) 계정을 만드는 설정 화면만 열린다 (`POST /auth/setup`).
- 최초 설정이 끝난 뒤에는 누구나 가입을 신청할 수 있다 (`POST /auth/signup`). 새 가입은 항상 일반 사용자·승인 대기이며
  세션을 주지 않는다. 최고 관리자가 승인해야 로그인·업무 기능을 쓸 수 있다.
- 계정 관리(가입 승인·반려, 이용 중지·재개, 비밀번호 초기화, 계정 추가)는 최고 관리자만 한다. 모든 변경은
  account_events에 남긴다 (같은 트랜잭션).
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    COOKIE_SECURE,
    SESSION_HOURS,
    STATUS_LABEL,
    AccountStatusError,
    AuthError,
    CurrentUser,
    CurrentUserDep,
    OwnerDep,
    check_password_rule,
    check_username_rule,
    current_user,
    end_sessions,
    hash_password,
    login,
    logout,
    role_label,
    set_status,
    verify_password,
)
from app.db import get_session
from app.models import AccountEvent, AppSetting, IntegrationRun, ReviewSession, SourceFile, User, ValidationRun
from app.timefmt import utc_to_kst

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
# 최초 소유자 설정이 끝났다는 표시 (app_settings 기본 키 → 동시 요청 중 하나만 성공).
# 마이그레이션 0015가 계정이 있는 기존 설치에도 넣어 설정 화면이 다시 열리지 않게 한다.
SETUP_KEY = "owner_setup"


class MeOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str


def me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        role_label=role_label(user.role),
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


class StatusOut(BaseModel):
    setup_required: bool
    user: MeOut | None = None  # 로그인돼 있으면 그 사용자 (로그인 전 화면이 401 없이 상태를 알 수 있게)


@router.get("/status", response_model=StatusOut)
def auth_status(request: Request, db: Annotated[Session, Depends(get_session)]) -> StatusOut:
    setup_required = (db.scalar(select(func.count(User.id))) or 0) == 0 and db.get(AppSetting, SETUP_KEY) is None
    try:
        row = db.get(User, current_user(request, db).id)
    except HTTPException:
        row = None
    return StatusOut(setup_required=setup_required, user=me_out(row) if row is not None else None)


def _conflict(exc: IntegrityError | OperationalError) -> bool:
    """동시 요청끼리의 충돌인지: 기본 키·유일 제약 위반, 또는 SQLite 쓰기 잠금(database is locked).
    그 밖의 OperationalError(연결 실패·인증 실패 등)는 충돌이 아니다."""
    return isinstance(exc, IntegrityError) or "database is locked" in str(getattr(exc, "orig", exc))


class SetupIn(BaseModel):
    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(max_length=128)


@router.post("/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def setup(body: SetupIn, response: Response, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    """계정이 하나도 없을 때만 최초 소유자 계정을 만든다.

    동시에 들어온 요청이 함께 '계정 0개'를 보더라도 소유자는 한 명만 생기도록, 계정과 같은 트랜잭션에서
    고정 키(app_settings.key = SETUP_KEY, 기본 키)를 넣는다. 늦은 요청은 기본 키 충돌로 실패해 409가 되고
    계정·세션을 남기지 않는다 (프로세스 안의 잠금이 아니라 DB 제약으로 막는다).
    """
    if (db.scalar(select(func.count(User.id))) or 0) > 0 or db.get(AppSetting, SETUP_KEY) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 계정이 있습니다. 로그인하세요.")
    username = body.username.strip().lower()
    try:
        check_username_rule(username)
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    password_hash = hash_password(body.password)
    try:
        db.add(AppSetting(key=SETUP_KEY, value=json.dumps({"owner": username}), updated_at=datetime.now(UTC)))
        db.flush()  # 다른 요청이 먼저 넣었으면 여기서(또는 커밋에서) 기본 키 충돌
        owner = User(
            username=username,
            display_name=body.display_name.strip(),
            password_hash=password_hash,
            role="owner",
            failed_logins=0,
        )
        set_status(owner, "active")  # 최초 소유자는 승인 대기 없이 바로 이용한다
        db.add(owner)
        db.flush()
        _event(db, None, owner, "setup", None, "active", after_role="owner", reason="최초 설정 — 최고 관리자 계정")
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if not _conflict(exc):
            raise  # DB에 닿지 못함 → 503 (main) — '이미 계정이 있음'으로 보이지 않게
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 계정이 있습니다. 로그인하세요.") from exc
    user, token = login(db, username, body.password)
    _set_cookie(response, token)
    return me_out(user)


class SignupIn(BaseModel):
    """공개 가입 신청. 역할·상태·활성 여부 같은 다른 필드는 받지 않는다 (보내면 422) — 서버가 정한다."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(max_length=128)
    password_confirm: str = Field(max_length=128)


class SignupOut(BaseModel):
    username: str
    status: str
    status_label: str
    message: str


SIGNUP_DONE = "가입 신청이 완료되었습니다. 최고 관리자의 승인 후 로그인할 수 있습니다."


@router.post("/signup", response_model=SignupOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, db: Annotated[Session, Depends(get_session)]) -> SignupOut:
    """본인 계정의 가입을 신청한다. 항상 일반 사용자·승인 대기로 저장하고 세션(쿠키)을 주지 않는다.

    최초 설정(최고 관리자 계정 만들기)이 끝나기 전에는 막는다 — 가입으로 계정을 먼저 만들어 최초 설정을 막지 못하게.
    아이디 중복은 DB 유일 조건으로도 막는다 (동시 가입 중 하나만 성공, 나머지 409).
    """
    if db.get(AppSetting, SETUP_KEY) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "최초 설정(최고 관리자 계정 만들기)이 끝난 뒤에 가입할 수 있습니다."
        )
    username = body.username.strip().lower()
    try:
        check_username_rule(username)
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.password != body.password_confirm:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "비밀번호 확인이 일치하지 않습니다.")
    taken = HTTPException(status.HTTP_409_CONFLICT, f"이미 사용 중인 아이디입니다: {username}")
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise taken
    user = User(
        username=username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role="user",
        failed_logins=0,
    )
    set_status(user, "pending")
    try:
        db.add(user)
        db.flush()
        _event(db, None, user, "signup", None, "pending", after_role="user", actor_self=True)
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if not _conflict(exc):
            raise  # DB에 닿지 못함 → 503 (main) — '이미 있는 아이디'로 보이지 않게
        raise taken from exc
    return SignupOut(username=username, status="pending", status_label=STATUS_LABEL["pending"], message=SIGNUP_DONE)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/login", response_model=MeOut)
def do_login(body: LoginIn, response: Response, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    try:
        user, token = login(db, body.username, body.password)
    except AccountStatusError as exc:  # 아이디·비밀번호가 맞을 때만 — 본인 계정의 상태 안내 (세션 없음)
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


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeIn, user: CurrentUserDep, response: Response, db: Annotated[Session, Depends(get_session)]
) -> Response:
    row = db.get(User, user.id)
    assert row is not None
    version = row.auth_version
    if not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status.HTTP_409_CONFLICT, "현재 비밀번호가 올바르지 않습니다.")
    try:
        check_password_rule(body.new_password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    # 확인한 그대로일 때만 바꾼다 (그 사이 초기화·중지가 커밋됐으면 0행). 버전을 올려 다른 세션·진행 중 로그인을 무효로
    changed = db.execute(
        update(User)
        .where(User.id == row.id, User.auth_version == version)
        .values(password_hash=hash_password(body.new_password), auth_version=User.auth_version + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "계정 정보가 바뀌었습니다. 다시 로그인한 뒤 시도하세요.")
    end_sessions(db, row.id)
    db.commit()
    db.refresh(row)
    # 다른 기기의 로그인은 끊고, 지금 화면은 새 세션으로 이어간다
    try:
        _, token = login(db, row.username, body.new_password)
    except AuthError as exc:  # 그 사이 이용 중지·초기화가 끼어든 경우: 변경은 반영됐고 새 세션만 만들지 않는다
        raise HTTPException(
            status.HTTP_409_CONFLICT, "비밀번호는 바뀌었지만 계정 상태가 바뀌어 다시 로그인해야 합니다."
        ) from exc
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(out, token)
    return out


# ---------------------------------------------------------------- 계정 관리 (최고 관리자)


def _event(
    db: Session,
    actor: CurrentUser | None,
    target: User,
    action: str,
    before: str | None,
    after: str | None,
    *,
    reason: str | None = None,
    result: str = "ok",
    before_role: str | None = None,
    after_role: str | None = None,
    actor_self: bool = False,
) -> None:
    """관리 이력 1건 (커밋은 호출한 쪽에서 계정 변경과 함께). 비밀번호·쿠키·토큰은 넣지 않는다."""
    db.add(
        AccountEvent(
            created_at=datetime.now(UTC),
            actor_id=target.id if actor_self else (actor.id if actor else None),
            actor_name=target.username if actor_self else (actor.username if actor else None),
            target_id=target.id,
            target_name=target.username,
            action=action,
            before_status=before,
            after_status=after,
            before_role=before_role,
            after_role=after_role,
            reason=reason,
            result=result,
        )
    )


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
    q: Annotated[str | None, Query(max_length=64)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
) -> list[UserOut]:
    """아이디·표시 이름 검색(q, 부분 일치), 상태 필터(status). 승인 대기 → 최근 가입 순."""
    stmt = select(User)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(User.username).like(like) | func.lower(User.display_name).like(like))
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    rows = sorted(db.scalars(stmt), key=lambda u: (u.status != "pending", -u.id))
    activity = last_activity(db)
    return [user_out(u, activity) for u in rows]


class UserIn(BaseModel):
    """최고 관리자가 직접 추가하는 계정: 항상 일반 사용자. 승인 여부(status)를 반드시 정한다."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    role: Literal["user"] = "user"
    status: Literal["active", "pending"]
    password: str = Field(max_length=128)


@users_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, actor: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> UserOut:
    username = body.username.strip().lower()
    try:
        check_username_rule(username)
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    taken = HTTPException(status.HTTP_409_CONFLICT, f"이미 있는 아이디입니다: {username}")
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise taken
    user = User(
        username=username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role="user",
        failed_logins=0,
    )
    set_status(user, body.status)
    try:
        db.add(user)
        db.flush()
        reason = "최고 관리자가 추가 — " + ("바로 이용(승인함)" if body.status == "active" else "승인 대기로 추가")
        _event(db, actor, user, "create", None, body.status, after_role="user", reason=reason)
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if not _conflict(exc):
            raise  # DB에 닿지 못함 → 503 (main) — '이미 있는 아이디'로 보이지 않게
        raise taken from exc
    return user_out(user)


class UserPatch(BaseModel):
    """표시 이름만 바꾼다. 역할·상태는 이 API로 바꾸지 않는다 (상태는 승인·반려·중지·재개 API로)."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=64)


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
TRANSITIONS: dict[str, tuple[str, str]] = {
    "approve": ("pending", "active"),
    "reject": ("pending", "rejected"),
    "suspend": ("active", "suspended"),
    "resume": ("suspended", "active"),
}
ACTION_LABEL = {"approve": "가입 승인", "reject": "가입 반려", "suspend": "이용 중지", "resume": "이용 재개"}


class StatusChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


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


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=128)


@users_router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int, body: PasswordResetIn, actor: OwnerDep, db: Annotated[Session, Depends(get_session)]
) -> Response:
    """새 비밀번호로 바꾸고(해시만 저장) 그 사용자의 세션을 모두 끝낸다. 기존 비밀번호는 조회할 수 없다."""
    user = _target(db, user_id)
    if user.role == "owner":
        raise _deny(db, actor, user, "password_reset", "최고 관리자 비밀번호는 '내 계정'에서 직접 바꾸세요.")
    try:
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            password_hash=hash_password(body.password),
            failed_logins=0,
            locked_until=None,
            auth_version=User.auth_version + 1,  # 진행 중이던 이전 비밀번호 로그인·기존 세션을 무효로
        )
        .execution_options(synchronize_session=False)
    )
    end_sessions(db, user.id)
    _event(db, actor, user, "password_reset", user.status, user.status, reason="비밀번호 초기화 · 기존 세션 종료")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
