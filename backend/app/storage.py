"""업로드 원본 보관. 원본은 새 이름으로 한 번만 쓰고 즉시 읽기 전용으로 만든다."""

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import BinaryIO

from app.config import DATA_DIR, MAX_UPLOAD_MIB

UPLOAD_DIR = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = MAX_UPLOAD_MIB * 2**20
_CHUNK = 1024 * 1024


class UploadTooLarge(Exception):
    pass


@dataclass
class StoredFile:
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int


def safe_original_name(filename: str | None) -> str:
    # 브라우저가 경로를 붙여 보내는 경우(C:\fakepath\a.xlsx 등) 파일명만 남긴다.
    name = PurePath((filename or "").replace("\\", "/")).name.strip()
    return name[:255] or "unnamed"


def store_original(src: BinaryIO, extension: str) -> StoredFile:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{extension}"
    digest = hashlib.sha256()
    size = 0
    try:
        # 'xb': 같은 이름이 이미 있으면 실패 → 기존 파일을 절대 덮어쓰지 않는다.
        with path.open("xb") as dst:
            while chunk := src.read(_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadTooLarge
                digest.update(chunk)
                dst.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return StoredFile(path, path.relative_to(DATA_DIR).as_posix(), digest.hexdigest(), size)
