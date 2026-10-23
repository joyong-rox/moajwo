from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app import models  # noqa: F401  (메타데이터에 모델 등록)
from app.config import MIGRATION_DATABASE_URL
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 외부 PostgreSQL에서 여러 인스턴스가 동시에 기동해도 마이그레이션은 하나씩 (세션 단위 잠금이라 연결 풀러를 거치지 않는
# 직접 연결이어야 한다 — MOAJWO_MIGRATION_DATABASE_URL)
MIGRATION_LOCK = 727_274


def run_migrations_offline() -> None:
    context.configure(url=MIGRATION_DATABASE_URL, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)  # fmt: skip
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 앱 엔진(app.db)이 아니라 마이그레이션 주소로 따로 연결한다 (외부 DB는 직접 연결·스키마 변경 권한 계정)
    engine = create_engine(MIGRATION_DATABASE_URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        if postgres:
            connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK})
            connection.commit()
        try:
            # SQLite는 ALTER TABLE 지원이 제한적이므로 batch 모드를 쓴다 (PostgreSQL은 그대로 ALTER).
            context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if postgres:
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK})
                connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
