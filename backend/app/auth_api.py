"""로그인·계정 API.

- 처음 실행(계정 0개)에는 최초 소유자 계정을 만드는 설정 화면만 열린다 (`POST /auth/setup`).
- 관리자는 일반 사용자 계정을, 소유자는 모든 계정을 관리한다.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    COOKIE_SECURE,
    ROLE_LABEL,
    SESSION_HOURS,
    AuthError,
    CurrentUserDep,
    ManagerDep,
    check_password_rule,
    check_username_rule,
    current_user,
    end_sessions,
    hash_password,
    login,
    logout,
    verify_password,
)
from app.db import get_session
from app.models import User
from app.timefmt import utc_to_kst

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])

Role = Literal["user", "admin", "owner"]


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
        role_label=ROLE_LABEL[user.role],
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
    setup_required = (db.scalar(select(func.count(User.id))) or 0) == 0
    try:
        row = db.get(User, current_user(request, db).id)
    except HTTPException:
        row = None
    return StatusOut(setup_required=setup_required, user=me_out(row) if row is not None else None)


class SetupIn(BaseModel):
    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(max_length=128)


@router.post("/setup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def setup(body: SetupIn, response: Response, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    """계정이 하나도 없을 때만 최초 소유자 계정을 만든다."""
    if (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 계정이 있습니다. 로그인하세요.")
    username = body.username.strip().lower()
    try:
        check_username_rule(username)
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.add(
        User(
            username=username,
            display_name=body.display_name.strip(),
            password_hash=hash_password(body.password),
            role="owner",
            is_active=True,
            failed_logins=0,
        )
    )
    db.commit()
    user, token = login(db, username, body.password)
    _set_cookie(response, token)
    return me_out(user)


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/login", response_model=MeOut)
def do_login(body: LoginIn, response: Response, db: Annotated[Session, Depends(get_session)]) -> MeOut:
    try:
        user, token = login(db, body.username, body.password)
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
    if not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status.HTTP_409_CONFLICT, "현재 비밀번호가 올바르지 않습니다.")
    try:
        check_password_rule(body.new_password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row.password_hash = hash_password(body.new_password)
    end_sessions(db, row.id)
    db.commit()
    # 다른 기기의 로그인은 끊고, 지금 화면은 새 세션으로 이어간다
    _, token = login(db, row.username, body.new_password)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(out, token)
    return out


# ---------------------------------------------------------------- 계정 관리 (관리자·소유자)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        role=u.role,
        role_label=ROLE_LABEL[u.role],
        is_active=u.is_active,
        created_at=utc_to_kst(u.created_at),
        last_login_at=utc_to_kst(u.last_login_at),
    )


def _check_manage(actor_role: str, target_role: str) -> None:
    """관리자는 일반 사용자 계정만, 소유자는 모든 계정을 관리한다."""
    if actor_role != "owner" and target_role != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "관리자·소유자 계정은 소유자만 관리할 수 있습니다.")


def _active_owners(db: Session) -> int:
    return db.scalar(select(func.count(User.id)).where(User.role == "owner", User.is_active.is_(True))) or 0


@users_router.get("", response_model=list[UserOut])
def list_users(_: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> list[UserOut]:
    return [user_out(u) for u in db.scalars(select(User).order_by(User.id))]


class UserIn(BaseModel):
    username: str = Field(max_length=32)
    display_name: str = Field(min_length=1, max_length=64)
    role: Role = "user"
    password: str = Field(max_length=128)


@users_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> UserOut:
    _check_manage(actor.role, body.role)
    username = body.username.strip().lower()
    try:
        check_username_rule(username)
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 있는 아이디입니다: {username}")
    user = User(
        username=username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        failed_logins=0,
    )
    db.add(user)
    db.commit()
    return user_out(user)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    role: Role | None = None
    is_active: bool | None = None


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserPatch, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "계정을 찾을 수 없습니다.")
    _check_manage(actor.role, user.role)
    if body.role is not None:
        _check_manage(actor.role, body.role)
    if user.id == actor.id and (body.role not in (None, user.role) or body.is_active is False):
        raise HTTPException(status.HTTP_409_CONFLICT, "자기 계정의 역할을 바꾸거나 비활성화할 수 없습니다.")
    losing_owner = (
        user.role == "owner" and user.is_active and (body.role not in (None, "owner") or body.is_active is False)
    )
    if losing_owner and _active_owners(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "활성 소유자 계정이 최소 1개 있어야 합니다.")
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            end_sessions(db, user.id)
    if body.role is not None or body.is_active is not None:
        end_sessions(db, user.id)  # 권한이 바뀌면 다시 로그인해서 새 권한으로 시작한다
    db.commit()
    return user_out(user)


class PasswordResetIn(BaseModel):
    password: str = Field(max_length=128)


@users_router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int, body: PasswordResetIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> Response:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "계정을 찾을 수 없습니다.")
    _check_manage(actor.role, user.role)
    try:
        check_password_rule(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    user.password_hash = hash_password(body.password)
    user.failed_logins = 0
    user.locked_until = None
    end_sessions(db, user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
