"""Initialize the disposable review owner before accepting public requests."""

import os

from fastapi import Response
from sqlalchemy import func, select

from app.auth import end_sessions, usable
from app.auth_api import SETUP_KEY, SetupIn, setup
from app.db import SessionLocal
from app.models import AppSetting, User


def main() -> None:
    if os.environ.get("MOAJWO_PREVIEW") != "1":
        raise RuntimeError("This entrypoint is only for a disposable review environment")
    username = os.environ.get("MOAJWO_PREVIEW_OWNER_USERNAME", "preview-owner").strip().lower()
    password = os.environ.get("MOAJWO_PREVIEW_OWNER_PASSWORD", "")
    if not 20 <= len(password) <= 128:
        raise RuntimeError("Set a private review owner password of 20 to 128 characters")

    with SessionLocal() as db:
        count = db.scalar(select(func.count(User.id))) or 0
        marker = db.get(AppSetting, SETUP_KEY)
        if count or marker is not None:
            owner = db.scalar(select(User).where(User.username == username))
            if marker is None or owner is None or owner.role != "owner" or not usable(owner):
                raise RuntimeError("Existing data is not the initialized review database")
            # Preserve all existing credentials and user changes on process restart.
            return
        owner = setup(
            SetupIn(username=username, display_name="검토용 관리자", password=password),
            Response(),
            db,
        )
        # setup() issues a session for interactive setup; there is no browser here.
        end_sessions(db, owner.id)
        db.commit()


if __name__ == "__main__":
    main()
