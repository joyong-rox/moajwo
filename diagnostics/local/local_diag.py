"""Docker 없는 로컬 격리 서버에서 진단 스크립트를 돌린다.

- diag_common.db(): docker exec 대신 로컬 SQLite를 읽기 전용(mode=ro)으로 연다 (ISO_DB 환경 변수).
- 요청 범위는 그대로 diag_scope (DIAG_URL = http://127.0.0.1:8086만).
사용법: python local_diag.py seed | f1 <출력 폴더> <라벨> | script <스크립트.py> [인자...]
"""

import json
import os
import runpy
import sqlite3
import sys
from pathlib import Path

DIAG = Path(os.environ["DIAG_SCRIPTS"])
sys.path.insert(0, str(DIAG))
import diag_common  # noqa: E402

DB = Path(os.environ["ISO_DB"]) if os.environ.get("ISO_DB") else None
PG = os.environ.get("ISO_PG_URL")  # 격리 PostgreSQL (앱 계정, 읽기 전용 트랜잭션으로만 연다)


def db(sql: str) -> list:
    if PG:
        import psycopg

        with psycopg.connect(PG, options="-c default_transaction_read_only=on", connect_timeout=10) as con:
            return json.loads(json.dumps([list(r) for r in con.execute(sql).fetchall()], default=str))
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        return json.loads(json.dumps(con.execute(sql).fetchall(), default=str))
    finally:
        con.close()


diag_common.db = db

if __name__ == "__main__":
    what = sys.argv[1]
    if what == "seed":
        import seed

        seed.setup_accounts(diag_common.creds())
        print("seed ok")
    elif what == "f1":
        sys.argv = ["f1_repro.py", *sys.argv[2:]]
        runpy.run_path(str(DIAG / "f1_repro.py"), run_name="__main__")
    elif what == "script":
        target = sys.argv[2]
        sys.argv = [target, *sys.argv[3:]]
        runpy.run_path(target, run_name="__main__")
