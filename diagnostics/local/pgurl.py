"""시험용 PostgreSQL 주소 만들기 (비밀번호는 퍼센트 인코딩 — 출력은 셸 변수로만 쓴다)."""
import os
import sys
from pathlib import Path
from urllib.parse import quote

SCR = Path(os.environ.get("R5_WORK") or Path(sys.argv[0]).resolve().parent)  # pgsecret/가 있는 작업 폴더
role, db = sys.argv[1], sys.argv[2]
pw = (SCR / "pgsecret" / {"moajwo_owner": "ownerpw.txt", "moajwo_app": "apppw.txt", "pgadmin": "superpw.txt"}[role]).read_text().strip()
extra = sys.argv[3] if len(sys.argv) > 3 else "sslmode=require&channel_binding=require"
print(f"postgresql://{role}:{quote(pw, safe='')}@127.0.0.1:55432/{db}?{extra}")
