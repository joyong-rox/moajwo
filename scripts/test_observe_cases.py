# scripts/test_observe.sh의 사례 — 합성 SQLite(관찰 도구가 읽는 표·열만)·합성 로그(docker logs -t 형식)·합성 반영 기록·기준선과 기대 문구를 사례 폴더에 만든다.
# 사용: python test_observe_cases.py <사례 이름|list> <폴더>   (운영·Docker·네트워크 없음. 값은 모두 합성.)
import json
import sqlite3
import sys
from pathlib import Path

START = "2026-10-31 07:58:32"  # 관찰 시작(UTC) — observe.sh 기본값과 같다
DEPLOY_LINE = "[16:58:32] 전환 완료: 리비전 0022_temp_passwords (head) · backend c5f2a4c2f0df · frontend 276195b7e4a8 · 중단 40초"
RECOVER_TEMP = "설치 담당자의 복구 명령(app.adminctl recover) — 비밀번호 재설정 · 세션 종료 · 임시 비밀번호(첫 로그인에서 변경)"
RECOVER_CHOSEN = "설치 담당자의 복구 명령(app.adminctl recover) — 비밀번호 재설정 · 세션 종료"
INFO = 'INFO:     172.19.0.3:50000 - "GET /health HTTP/1.1" 200 OK'
LOGIN200 = 'INFO:     172.19.0.3:50001 - "POST /auth/login HTTP/1.1" 200 OK'
LOGIN401 = 'INFO:     172.19.0.3:50002 - "POST /auth/login HTTP/1.1" 401 Unauthorized'


def owner(last_login=None, temp=False):
    return dict(id=1, role="owner", status="active", is_active=1, must_change_password=int(temp), failed_logins=0, locked_until=None, last_login_at=last_login, created_at="2026-10-09 08:14:43")


def user(uid=2, temp=False, locked_until=None, status="active"):
    return dict(id=uid, role="user", status=status, is_active=1, must_change_password=int(temp), failed_logins=0, locked_until=locked_until, last_login_at=None, created_at="2026-10-10 00:00:00")


def sess(created, expires, uid=1):
    return dict(user_id=uid, created_at=created, expires_at=expires)


def ev(action, created, target=2, reason=None, result="ok"):
    return dict(target_id=target, action=action, result=result, reason=reason, created_at=created)


def tlog(ts, line):
    return f"{ts}Z {line}" if not ts.endswith("Z") else f"{ts} {line}"


CASES = {
    # 같은 날 과거·미래: 같은 날짜의 만료 시각이 지금보다 앞/뒤 — isoformat()의 'T' 비교였다면 둘 다 0으로 셌다
    "sameday": dict(
        now="2026-10-31T11:50:27Z",
        users=[owner("2026-10-31 11:49:06.899684")],
        sessions=[sess("2026-10-31 11:46:14.037771", "2026-10-31 23:46:14.037771"), sess("2026-10-31 08:00:00", "2026-10-31 10:00:00")],
        logs_b=[tlog("2026-10-31T11:46:14.235378747", LOGIN200), tlog("2026-10-31T11:49:06.945120643", LOGIN200)],
        expect=[
            "1 소유자 로그인 | 근거 | 반영 뒤 소유자 로그인 기록 있음 · 반영 뒤 만든 세션 2개 · 지금 유효 세션 1개",
            "5 로그인 실패 급증 없음 | 통과 | 관찰 시작 이후 로그인 401 0건(200 2건) · 최근 24시간 401 0건 · 지금 잠긴 계정 0",
            "3 로그 오류 0 | 통과 |",
            "6 DB 무결성 | 통과 | integrity_check ok · 리비전 0022_temp_passwords",
            "7 임시 상태 계정 | 통과 | 지금 이용 중 임시 0 · 그 밖(중지 등) 임시 0 · 관찰 중 발급·재발급 0건(발급 0 · 재발급 0 · 복구 명령 임시 0; 미변경 0, 24시간 초과 0)",
            "4 가입 410 유지 | 건너뜀 |",
            "8 복구 사건 0 | 건너뜀 |",
            "== 자동 판정: 통과 4 · 주의 0 · 실패 0 · 건너뜀 2",
        ],
        expect_json=['"sessions_active_now": 1', '"basis": "UTC 단일 기준'],
    ),
    # 자정 경계: 날짜가 바뀐 직후 — 어제 23:59:59 만료는 끝, 오늘 00:00:31 만료는 유효
    "midnight_after": dict(
        now="2026-11-01T00:00:30Z",
        users=[owner("2026-10-31 11:49:06")],
        sessions=[sess("2026-10-31 11:00:00", "2026-10-31 23:59:59"), sess("2026-10-31 12:00:00", "2026-11-01 00:00:31")],
        expect=["반영 뒤 만든 세션 2개 · 지금 유효 세션 1개"],
    ),
    # 자정 경계: 날짜가 바뀌기 직전 — 00:00:00 만료는 아직 유효, 23:59:58 만료는 끝
    "midnight_before": dict(
        now="2026-10-31T23:59:59Z",
        users=[owner("2026-10-31 11:49:06")],
        sessions=[sess("2026-10-31 12:00:00", "2026-11-01 00:00:00"), sess("2026-10-31 11:00:00", "2026-10-31 23:59:58")],
        expect=["반영 뒤 만든 세션 2개 · 지금 유효 세션 1개"],
    ),
    # 24시간 경계 — 발급 09:00 → 본인 변경 23:59 뒤: 초과 아님
    "issue_2359": dict(
        now="2026-11-02T00:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user()],
        events=[ev("provision", "2026-10-31 09:00:00"), ev("first_password_change", "2026-11-01 08:59:00")],
        expect=["7 임시 상태 계정 | 통과 | 지금 이용 중 임시 0 · 그 밖(중지 등) 임시 0 · 관찰 중 발급·재발급 1건(발급 1 · 재발급 0 · 복구 명령 임시 0; 미변경 0, 24시간 초과 0)"],
        expect_json=['"over_24h": 0', '"open_hours": 23.98'],
    ),
    # 24시간 경계 — 정확히 24:00 뒤 변경: '넘긴' 것이 아니다
    "issue_2400": dict(
        now="2026-11-02T00:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user()],
        events=[ev("provision", "2026-10-31 09:00:00"), ev("first_password_change", "2026-11-01 09:00:00")],
        expect=["7 임시 상태 계정 | 통과 |", "24시간 초과 0)"],
        expect_json=['"over_24h": 0', '"open_hours": 24.0'],
    ),
    # 24시간 경계 — 24:01 뒤 변경: 넘김 → 실패
    "issue_2401": dict(
        now="2026-11-02T00:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user()],
        events=[ev("provision", "2026-10-31 09:00:00"), ev("first_password_change", "2026-11-01 09:01:00")],
        expect=["7 임시 상태 계정 | 실패 | 지금 이용 중 임시 0 · 그 밖 임시 0 · 발급·재발급 1건(발급 1 · 재발급 0 · 복구 명령 임시 0; 미변경 0, 24시간 초과 1)"],
        expect_json=['"over_24h": 1', '"open_hours": 24.02'],
        exit_nonzero=True,
    ),
    # 아직 변경하지 않은 재발급(판정 시점 이용 중 임시 1) — 24시간 안이라도 '지금 임시'로 실패
    "issue_open": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user(temp=True)],
        events=[ev("password_reset", "2026-10-31 10:00:00")],
        expect=["7 임시 상태 계정 | 실패 | 지금 이용 중 임시 1 · 그 밖 임시 0 · 발급·재발급 1건(발급 0 · 재발급 1 · 복구 명령 임시 0; 미변경 1, 24시간 초과 0)"],
        exit_nonzero=True,
    ),
    # 만료 세션·잠금 해제 직전: 1초 뒤 만료·해제 → 아직 유효·아직 잠김(주의)
    "expiry_before": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user(locked_until="2026-10-31 12:00:01")],
        sessions=[sess("2026-10-31 11:00:00", "2026-10-31 12:00:01")],
        expect=["지금 유효 세션 1개", "5 로그인 실패 급증 없음 | 주의 | 최근 24시간 로그인 401 0건 · 잠긴 계정 1"],
    ),
    # 만료 세션·잠금 해제 직후: 1초 전 만료·해제 → 끝·풀림(통과). 정확히 지금 만료도 '유효 아님'
    "expiry_after": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06"), user(locked_until="2026-10-31 11:59:59")],
        sessions=[sess("2026-10-31 11:00:00", "2026-10-31 11:59:59"), sess("2026-10-31 11:30:00", "2026-10-31 12:00:00")],
        expect=["반영 뒤 만든 세션 2개 · 지금 유효 세션 0개", "5 로그인 실패 급증 없음 | 통과 |", "지금 잠긴 계정 0"],
    ),
    # admin_recover 발급 — 생성 비밀번호(사유에 '임시 비밀번호'): 발급으로 센다. 판정 시점 임시 1 → 실패
    "recover_generated": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06", temp=True)],
        events=[ev("admin_recover", "2026-10-31 10:00:00", target=1, reason=RECOVER_TEMP)],
        expect=["7 임시 상태 계정 | 실패 | 지금 이용 중 임시 1 · 그 밖 임시 0 · 발급·재발급 1건(발급 0 · 재발급 0 · 복구 명령 임시 1; 미변경 1, 24시간 초과 0)"],
        expect_json=['"admin_recover": 1'],
        exit_nonzero=True,
    ),
    # admin_recover — 담당자가 고른 비밀번호(임시 상태 아님): 발급으로 세지 않는다. 이력 집계에는 남는다
    "recover_chosen": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        events=[ev("admin_recover", "2026-10-31 10:00:00", target=1, reason=RECOVER_CHOSEN)],
        expect=["7 임시 상태 계정 | 통과 |", "관찰 중 발급·재발급 0건(발급 0 · 재발급 0 · 복구 명령 임시 0; 미변경 0, 24시간 초과 0) · 관찰 중 계정 이력 {'admin_recover': 1}"],
    ),
    # admin_recover 생성 비밀번호 → 같은 날 본인 변경(닫힘) → 통과
    "recover_closed": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        events=[ev("admin_recover", "2026-10-31 09:00:00", target=1, reason=RECOVER_TEMP), ev("first_password_change", "2026-10-31 09:30:00", target=1)],
        expect=["7 임시 상태 계정 | 통과 |", "관찰 중 발급·재발급 1건(발급 0 · 재발급 0 · 복구 명령 임시 1; 미변경 0, 24시간 초과 0)"],
    ),
    # 반영 전 로그인 제외 — 시작 전 last_login·세션·로그인 로그는 관찰에 넣지 않는다(지금 유효 세션은 시각만 본다)
    "predeploy": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 06:00:00")],
        sessions=[sess("2026-10-31 06:00:00", "2026-10-31 18:00:00")],
        logs_b=[tlog("2026-10-31T06:00:00.000000000", LOGIN200), tlog("2026-10-31T06:30:00.000000000", LOGIN401), tlog("2026-10-31T07:58:31.999999999", LOGIN401), tlog("2026-10-31T08:00:00.000000000", INFO)],
        expect=["1 소유자 로그인 | 근거 | 반영 뒤 소유자 로그인 기록 없음 · 반영 뒤 만든 세션 0개 · 지금 유효 세션 1개", "관찰 시작 이후 로그인 401 0건(200 0건) · 최근 24시간 401 0건"],
    ),
    # 시작 시각과 같은 초의 로그인은 포함(경계 >=), '지금' 뒤의 줄은 제외(고정 시각 시험에서만 생길 수 있는 미래 줄)
    "start_edge": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 07:58:32")],
        logs_b=[tlog("2026-10-31T07:58:32.000000001", LOGIN200), tlog("2026-10-31T12:00:00.500000000", LOGIN200), tlog("2026-10-31T12:00:01.000000000", LOGIN401)],
        expect=["반영 뒤 소유자 로그인 기록 있음", "관찰 시작 이후 로그인 401 0건(200 2건)"],
    ),
    # ERROR: 줄 — uvicorn 머리말·Traceback·앱 실패 기록(머리말 없는 메시지)·5xx·nginx error 모두 세어 실패
    "errors": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        logs_b=[
            tlog("2026-10-31T09:00:00.000000000", "ERROR:    Exception in ASGI application"),
            tlog("2026-10-31T09:00:00.100000000", "Traceback (most recent call last):"),
            tlog("2026-10-31T09:00:00.200000000", '  File "/app/app/main.py", line 1, in f'),
            tlog("2026-10-31T09:00:01.000000000", "integrate run 5 failed: ValueError at integrate.py:10 run_one"),
            tlog("2026-10-31T09:00:02.000000000", 'INFO:     172.19.0.3:50003 - "POST /files HTTP/1.1" 500 Internal Server Error'),
            tlog("2026-10-31T09:00:03.000000000", "WARNING:  경고 줄은 오류로 세지 않는다"),
            tlog("2026-10-31T09:00:04.000000000", 'INFO:     172.19.0.3:50004 - "GET /files?name=ERRORS.xlsx HTTP/1.1" 200 OK'),
        ],
        logs_f=[
            tlog("2026-10-31T09:00:05.000000000", "2026/10/31 09:00:05 [error] 12#12: *1 upstream prematurely closed connection"),
            tlog("2026-10-31T09:00:06.000000000", '172.19.0.1 - - [31/Oct/2026:09:00:06 +0000] "GET /api/health HTTP/1.1" 502 157 "-" "curl"'),
        ],
        expect=["3 로그 오류 0 | 실패 | Traceback 1 · ERROR 1 · 앱 실패 기록 1 · 5xx 2 · nginx error 1"],
        exit_nonzero=True,
    ),
    # 오류 줄이 관찰 시작 전에만 있으면 통과(시작 전 줄은 창 밖)
    "errors_before_start": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        logs_b=[tlog("2026-10-31T07:00:00.000000000", "ERROR:    Exception in ASGI application"), tlog("2026-10-31T07:00:00.100000000", "Traceback (most recent call last):")],
        expect=["3 로그 오류 0 | 통과 | backend Traceback 0 · ERROR 0 · 앱 실패 기록 0 · 5xx 0 · nginx error 0 (backend 0줄"],
    ),
    # 최근 24시간 창 — 19건은 통과, 24시간 밖의 1건은 '관찰 이후'에만 든다
    "login401_19": dict(
        now="2026-11-02T00:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        logs_b=[tlog("2026-10-31T10:00:00.000000000", LOGIN401)] + [tlog(f"2026-11-01T23:00:{i:02d}.000000000", LOGIN401) for i in range(19)],
        expect=["5 로그인 실패 급증 없음 | 통과 | 관찰 시작 이후 로그인 401 20건(200 0건) · 최근 24시간 401 19건 · 지금 잠긴 계정 0"],
    ),
    # 최근 24시간 20건 → 주의 (24시간 경계 바로 안쪽 1건 포함)
    "login401_20": dict(
        now="2026-11-02T00:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        logs_b=[tlog("2026-11-01T00:00:00.000000000", LOGIN401)] + [tlog(f"2026-11-01T23:00:{i:02d}.000000000", LOGIN401) for i in range(19)],
        expect=["5 로그인 실패 급증 없음 | 주의 | 최근 24시간 로그인 401 20건 · 잠긴 계정 0 · 관찰 이후 401 20건(200 0건)"],
    ),
    # 업무 흐름 근거·행 수 추이 — 기준선 대비 바뀐 표만 적는다
    "flow": dict(
        now="2026-10-31T12:00:00Z",
        users=[owner("2026-10-31 11:49:06")],
        flow=dict(
            source_files=["2026-10-31 11:49:11"],
            mapping_decisions=["2026-10-31 11:49:21"] * 3,
            review_sessions=["2026-10-31 11:49:23"],
            runs=[("2026-10-31 11:49:25", "done"), ("2026-10-31 07:00:00", "done")],
            analysis_runs=["2026-10-15 10:36:02"],
        ),
        baseline=dict(alembic_version=1, users=1, auth_sessions=0, account_events=0, source_files=0, mapping_decisions=0, review_sessions=0, runs=1, analysis_runs=1),
        expect=[
            "2 업무 흐름 1회 | 근거 | 반영 뒤 업로드 1 · 매핑 확정 3 · 검토 1 · 통합 실행 1(완료 1) · 분석 0 → 업무 흐름 완료 기록 1건",
            "반영 직전 대비 행 수가 바뀐 표: {'mapping_decisions': [0, 3], 'review_sessions': [0, 1], 'runs': [1, 2], 'source_files': [0, 1]}",
        ],
    ),
}

SCHEMA = """
create table alembic_version (version_num text not null);
create table users (id integer primary key, username text, role text, status text, is_active integer, must_change_password integer,
                    failed_logins integer, locked_until text, last_login_at text, created_at text);
create table auth_sessions (id integer primary key, user_id integer, created_at text, expires_at text);
create table account_events (id integer primary key, target_id integer, action text, result text, reason text, created_at text);
create table source_files (id integer primary key, uploaded_at text);
create table mapping_decisions (id integer primary key, decided_at text);
create table review_sessions (id integer primary key, created_at text);
create table runs (id integer primary key, status text, created_at text);
create table analysis_runs (id integer primary key, created_at text);
"""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")  # 비교 문구는 LF로만 — Windows의 CRLF 변환을 막는다


def build(name: str, folder: Path) -> None:
    case = CASES[name]
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "synthetic.db"
    if db.exists():
        db.unlink()
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    c.execute("insert into alembic_version values (?)", ("0022_temp_passwords",))
    for u in case.get("users", []):
        c.execute(
            "insert into users (id, username, role, status, is_active, must_change_password, failed_logins, locked_until, last_login_at, created_at) "
            "values (:id, 'synthetic', :role, :status, :is_active, :must_change_password, :failed_logins, :locked_until, :last_login_at, :created_at)",
            u,
        )
    for s_ in case.get("sessions", []):
        c.execute("insert into auth_sessions (user_id, created_at, expires_at) values (:user_id, :created_at, :expires_at)", s_)
    for e in case.get("events", []):
        c.execute("insert into account_events (target_id, action, result, reason, created_at) values (:target_id, :action, :result, :reason, :created_at)", e)
    flow = case.get("flow", {})
    for v in flow.get("source_files", []):
        c.execute("insert into source_files (uploaded_at) values (?)", (v,))
    for v in flow.get("mapping_decisions", []):
        c.execute("insert into mapping_decisions (decided_at) values (?)", (v,))
    for v in flow.get("review_sessions", []):
        c.execute("insert into review_sessions (created_at) values (?)", (v,))
    for v, st in flow.get("runs", []):
        c.execute("insert into runs (status, created_at) values (?, ?)", (st, v))
    for v in flow.get("analysis_runs", []):
        c.execute("insert into analysis_runs (created_at) values (?)", (v,))
    c.commit()
    c.close()
    write(folder / "backend.log", "\n".join(case.get("logs_b", [tlog("2026-10-31T08:00:00.000000000", INFO)])) + "\n")
    write(folder / "frontend.log", "\n".join(case.get("logs_f", [])) + ("\n" if case.get("logs_f") else ""))
    write(folder / "deploy.log", DEPLOY_LINE + "\n")
    base = case.get("baseline")
    if base is None:  # 기본 기준선 = 지금 행 수 그대로(바뀐 표 없음)
        c = sqlite3.connect(db)
        base = {n: c.execute(f"select count(*) from [{n}]").fetchone()[0] for (n,) in c.execute("select name from sqlite_master where type = 'table' and name not like 'sqlite_%'")}
        c.close()
    write(folder / "baseline.txt", "".join(f"rows {k} {v}\n" for k, v in sorted(base.items())))
    write(folder / "now.txt", case["now"] + "\n")
    write(folder / "expect.txt", "\n".join(case.get("expect", [])) + "\n")
    write(folder / "expect_json.txt", "\n".join(case.get("expect_json", [])) + ("\n" if case.get("expect_json") else ""))
    write(folder / "exit.txt", ("nonzero" if case.get("exit_nonzero") else "zero") + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "list":
        print("\n".join(CASES))
    else:
        build(sys.argv[1], Path(sys.argv[2]))
        print(json.dumps({"case": sys.argv[1], "now": CASES[sys.argv[1]]["now"]}, ensure_ascii=False))
