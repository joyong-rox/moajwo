import os
import tempfile

# app 모듈을 임포트하기 전에 테스트 전용 데이터 디렉터리를 지정한다 (실제 DB 보호).
os.environ["MOAJWO_DATA_DIR"] = tempfile.mkdtemp(prefix="moajwo-test-")
os.environ.pop("DATABASE_URL", None)

import pytest  # noqa: E402

TEST_PASSWORD = "test-password-1"


@pytest.fixture(scope="session", autouse=True)
def logged_in_owner():
    """기존 기능 테스트는 소유자 계정으로 로그인한 상태로 실행한다 (로그인 자체는 test_auth에서 검사)."""
    from app.auth import CurrentUser, current_user, hash_password
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models import User

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = User(
            username="test-owner",
            display_name="테스트 소유자",
            password_hash=hash_password(TEST_PASSWORD),
            role="owner",
            is_active=True,
            failed_logins=0,
        )
        db.add(user)
        db.commit()
        owner = CurrentUser(user.id, user.username, user.display_name, user.role)
    app.dependency_overrides[current_user] = lambda: owner
    yield owner
    app.dependency_overrides.pop(current_user, None)
