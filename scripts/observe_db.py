# 관찰 점검(scripts/observe.sh)의 DB 재료 — 읽기 전용. 운영에서는 observe.sh가 데이터 볼륨을 읽기 전용으로 붙인 one-off 컨테이너의
# python에 이 파일을 표준 입력으로 넘겨 실행하고, 시험(scripts/test_observe.sh)에서는 호스트 python으로 합성 DB에 실행한다.
# 출력은 JSON 한 줄 — 건수·상태·시각만 (아이디·표시 이름·비밀번호·쿠키·업무 자료 내용은 읽지도 쓰지도 않는다).
#
# 시각 기준 (2026-10-31 7-2차, backups/followup-20261030/round7/observe-fix/TIMEZONES.md):
#   이 도구가 읽는 시각 열은 모두 **시각대 없는 UTC 문자열** "YYYY-MM-DD HH:MM:SS[.ffffff]"로 저장된다 (server_default CURRENT_TIMESTAMP는 초까지,
#   앱이 datetime.now(UTC)로 넣는 값은 마이크로초까지). KST 벽시계인 열(runs.started_at·finished_at, automations.updated_at)은 여기서 쓰지 않는다.
#   그래서 비교는 모두 같은 형식의 UTC 문자열 한 기준으로 한다 — isoformat()의 'T'(공백보다 커서 같은 날짜의 값을 모두 과거로 만든다)나
#   "KST일지도 모른다"는 완화(-9시간·두 기준 최댓값)는 쓰지 않는다.
# 환경 변수: DB_PATH(기본 /data/moajwo.db) · START_UTC(관찰 시작, 필수) · NOW_UTC(고정 시각 — 시험용, 비우면 지금) · BASE_ROWS("rows <표> <n>;" 목록)
#   OBS_MARKERS_PATH — 이력 사유 표식 모듈(backend/app/account_markers.py)의 경로. 표식 문자열은 그 파일 한 곳에서만 정의한다(앱·관찰 도구·시험 사례가 같이 씀).
import importlib.util
import json
import os
import re
import sqlite3
from datetime import UTC, datetime

FMT = "%Y-%m-%d %H:%M:%S"

MARKERS = os.environ.get("OBS_MARKERS_PATH", "/app/app/account_markers.py")  # observe.sh가 넘긴다: 운영은 저장소 파일을 컨테이너에 ro로 붙인 경로, 시험은 저장소 경로
_spec = importlib.util.spec_from_file_location("account_markers", MARKERS)
if _spec is None or _spec.loader is None or not os.path.isfile(MARKERS):
    raise SystemExit(f"[중단] 이력 표식 모듈을 읽을 수 없음: {MARKERS} (OBS_MARKERS_PATH)")
_markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_markers)
GENERATED_TEMP_PASSWORD_NOTE: str = _markers.GENERATED_TEMP_PASSWORD_NOTE


def parse_utc(text: str) -> datetime:
    d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)


def ts(value) -> datetime | None:
    """저장값 → UTC datetime (시각대 없는 값은 UTC로 본다 — 표별 확인 결과)."""
    if value is None:
        return None
    return parse_utc(str(value))


start = parse_utc(os.environ["START_UTC"])
now = parse_utc(os.environ["NOW_UTC"]) if os.environ.get("NOW_UTC") else datetime.now(UTC)
START_S, NOW_S = start.strftime(FMT), now.strftime(FMT)  # 저장 형식과 같은 꼴 — 초 단위 경계는 '>=' / '>'가 그대로 맞는다
db_path = os.environ.get("DB_PATH", "/data/moajwo.db")
uri = "file:" + ("///" + db_path if re.match(r"^[A-Za-z]:", db_path) else db_path) + "?mode=ro"
c = sqlite3.connect(uri, uri=True)


def since(table: str, col: str, where: str = "1=1", params: tuple = ()) -> int:
    return c.execute(f"select count(*) from {table} where {where} and {col} >= ?", (*params, START_S)).fetchone()[0]


out: dict = {
    "basis": "UTC 단일 기준(저장 형식과 같은 문자열 비교)",
    "start_utc": start.isoformat(timespec="seconds"),
    "now_utc": now.isoformat(timespec="seconds"),
    "revision": c.execute("select version_num from alembic_version").fetchone()[0],
    "integrity": c.execute("pragma integrity_check").fetchone()[0],
}
names = [r[0] for r in c.execute("select name from sqlite_master where type = ? and name not like ? order by name", ("table", "sqlite_%"))]
rows = {n: c.execute("select count(*) from [" + n + "]").fetchone()[0] for n in names}
base = dict((p.split(" ")[1], int(p.split(" ")[2])) for p in os.environ.get("BASE_ROWS", "").split(";") if p.strip())
out["rows"] = rows
out["rows_changed_vs_baseline"] = {n: [base.get(n), rows[n]] for n in rows if base and base.get(n) != rows[n]}

# 1 소유자 로그인 근거 · 2 업무 흐름 근거
owner_login = c.execute("select last_login_at from users where role = ? order by last_login_at desc limit 1", ("owner",)).fetchone()
out["owner_login_since_start"] = bool(owner_login and owner_login[0] and ts(owner_login[0]) >= start)
out["sessions_since_start"] = since("auth_sessions", "created_at")
out["sessions_active_now"] = c.execute("select count(*) from auth_sessions where expires_at > ?", (NOW_S,)).fetchone()[0]
out["flow_since_start"] = {
    "uploads": since("source_files", "uploaded_at"),
    "mapping_decisions": since("mapping_decisions", "decided_at"),
    "review_sessions": since("review_sessions", "created_at"),
    "runs_created": since("runs", "created_at"),
    "runs_done": since("runs", "created_at", "status = ?", ("done",)),
    "analysis_runs": since("analysis_runs", "created_at"),
}
# 5 로그인 실패 재료
out["locked_accounts_now"] = c.execute("select count(*) from users where locked_until is not null and locked_until > ?", (NOW_S,)).fetchone()[0]
out["failed_logins_counter_sum"] = c.execute("select coalesce(sum(failed_logins), 0) from users").fetchone()[0]
# 7 임시 상태 계정 — 지금 상태와 관찰 중 이력. 임시 상태를 만드는 경로 전수(PATHS.md): 계정 발급(provision) · 재발급(password_reset) ·
#   설치 담당자 복구 명령(admin_recover — 생성 비밀번호일 때만 임시 상태; 이력 사유에 "임시 비밀번호" 표식). 닫는 이력: 본인 변경(first_password_change) · 폐기(password_revoke).
out["temp_now"] = {
    "active": c.execute("select count(*) from users where must_change_password and status = ?", ("active",)).fetchone()[0],
    "other": c.execute("select count(*) from users where must_change_password and status != ?", ("active",)).fetchone()[0],
}
ISSUE_ACTIONS = ("provision", "password_reset", "admin_recover", "setup")  # setup = 설치 초기화 명령(init --generate) — 설치 뒤에는 생기지 않지만 전수를 위해 둔다
CLOSE_ACTIONS = ("first_password_change", "password_revoke")
ev = c.execute(
    "select target_id, action, created_at, coalesce(reason, '') from account_events where action in (?, ?, ?, ?, ?, ?) and result = ? order by id",
    (*ISSUE_ACTIONS, *CLOSE_ACTIONS, "ok"),
).fetchall()


def is_issue(action: str, reason: str) -> bool:
    if action in ("provision", "password_reset"):
        return True
    # 설치 담당자 명령(init·recover)은 생성 비밀번호로 임시 상태가 된 경우만 — 표식은 account_markers.py 한 곳에서 정의
    return action in ("admin_recover", "setup") and GENERATED_TEMP_PASSWORD_NOTE in reason


# 열린 시간 계산(보수적): 발급 이력마다 같은 계정의 '다음' 닫는 이력(본인 변경·폐기)까지를 잰다. 본인 변경 없이 재발급이 이어지면 첫 발급의
# 열린 시간은 그 뒤 닫는 이력까지로 재어 실제보다 길게 계산될 수 있다(과대 계산). 판정 로직은 그대로 둔다(7-3차 결정) — 발급이 겹치는 상황 자체가
# 런북 8절의 원칙 위반이고, 과대 계산은 '통과'를 '실패'로 바꿀 뿐 그 반대는 없으므로 놓치는 쪽으로 틀리지 않는다.
issues = []
kinds = {"provision": 0, "password_reset": 0, "admin_recover": 0, "setup": 0}
for tid, action, created, reason in ev:
    t = ts(created)
    if t is None or not is_issue(action, reason) or t < start:
        continue
    kinds[action] += 1
    close = next((ts(c2) for t2, a2, c2, _r in ev if t2 == tid and a2 in CLOSE_ACTIONS and ts(c2) > t), None)
    hours = ((close or now) - t).total_seconds() / 3600
    issues.append({"kind": action, "closed": close is not None, "open_hours": round(hours, 2), "over_24h": hours > 24})
out["issues_since_start"] = {
    "count": len(issues),
    "kinds": kinds,
    "over_24h": sum(1 for i in issues if i["over_24h"]),
    "still_open": sum(1 for i in issues if not i["closed"]),
    "items": issues,
}
out["events_since_start"] = dict(c.execute("select action, count(*) from account_events where created_at >= ? group by action", (START_S,)).fetchall())
print(json.dumps(out, ensure_ascii=False))
