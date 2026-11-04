"""저장한 작업(공통 key 연결 설정 재사용, app/keyjoin_jobs.py): 저장·목록·조회·갱신·삭제, 소유자만(다른 사용자·
최고 관리자 404), 새 파일 맞추기(파일 번호·이름·열 순서가 바뀐 파일, 열 누락·같은 이름 열·자료형 변화·같은 점수의
파일), 설정을 바꾼 뒤 오래된 확인 결과로 만들기 거절, 값·순서를 바꾼 변형 사본이 옛 결과로 오인되지 않음.
합성 자료만 쓴다(실제 기관 자료 아님). 거절·맞추기는 실행 기록·결과 파일을 남기지 않는다(_state로 단언)."""

import io
import threading
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import keyjoin_jobs
from app.keyjoin_jobs import kind_changes
from tests.conftest import db_rows
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, _provision_user, app_real_login  # noqa: F401
from tests.test_auth_races_api import _checked_out, _record
from tests.test_auth_races_api import _run as _run_threads
from tests.test_keyjoin import (  # noqa: F401
    A_ROWS,
    B_ROWS,
    _body,
    _csv,
    _grid,
    _input,
    _plan,
    _run,
    _state,
    _upload,
    _xlsx_bytes,
)

# 저장한 작업으로 다시 만든 결과의 기대값 (A 기준 범위, 가져올 열 = A의 부서, B의 교육 건수 → '교육건수') —
# 손으로 센 상수
HEADER = ["사번", "부서", "교육건수"]
EXPECTED = [("001", "총무", 10), ("002", "기획", None), ("003", "정보", 30)]
# B의 값을 바꾸고 행 순서를 섞은 변형 사본 — 옛 파일(B_ROWS)과 다른 값·다른 원본 행 번호가 그대로 결과에 나와야 한다
B_MUTATED = [["사용자번호", "교육 건수", "메모"], ["004", "44", "m4"], ["003", "33", "m3"], ["001", "11", "m1"]]
EXPECTED_MUTATED = [("001", "총무", 11), ("002", "기획", None), ("003", "정보", 33)]
EXPECTED_MUTATED_SOURCES = [[1, 2, 4], [2, 3, None], [3, 4, 3]]


def _job(c: TestClient, name: str, body: dict[str, Any], code: int = 201) -> dict[str, Any]:
    res = c.post("/keyjoin/jobs", json={"name": name, "request": body})
    assert res.status_code == code, res.text
    return res.json()


def _match(c: TestClient, job_id: int, fids: list[int], code: int = 200) -> dict[str, Any]:
    res = c.post(f"/keyjoin/jobs/{job_id}/match", json={"file_ids": fids})
    assert res.status_code == code, res.text
    return res.json()


def _apply(c: TestClient, job_id: int, assignments: list[dict[str, Any]], code: int = 200) -> dict[str, Any]:
    res = c.post(f"/keyjoin/jobs/{job_id}/apply", json={"assignments": assignments})
    assert res.status_code == code, res.text
    return res.json()


def _make(c: TestClient, body: dict[str, Any]) -> tuple[dict[str, Any], openpyxl.Workbook]:
    plan = _plan(c, body)
    assert plan["ready"], plan["problems"]
    res = _run(c, body, plan["basis"])
    assert res.status_code == 201, res.text
    dl = c.get(f"/runs/{res.json()['id']}/download")
    assert dl.status_code == 200
    return res.json(), openpyxl.load_workbook(io.BytesIO(dl.content))


def _rows(wb: openpyxl.Workbook) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _grid(wb["연결 결과"])[1:]]


@pytest.fixture(scope="module")
def kj(app_real_login, owner) -> dict[str, TestClient]:  # noqa: F811
    """이 파일만의 계정(test_keyjoin의 kj와 섞이지 않게) — 소유자·일반 사용자 둘."""
    made = {"owner": owner}
    for name in ("kjj-user1", "kjj-user2"):
        _provision_user(owner, app_real_login, name, "pass-" + name, name.upper())
        made[name] = _client(app_real_login, name, "pass-" + name)
    return made


@pytest.fixture(scope="module")
def ab(kj) -> dict[str, Any]:  # noqa: F811
    """kj-user1의 A.csv(기준)·B.xlsx(교육 시트)와, B의 '교육 건수'를 '교육건수'로 가져오는 요청 본문."""
    u = kj["kjj-user1"]
    a = _csv(u, "A.csv", A_ROWS)
    b = _upload(u, "B.xlsx", _xlsx_bytes({"교육": B_ROWS}))
    body = _body([_input(a, cols=("c1",)), _input(b, cols=("c1",), names={"c1": "교육건수"})])
    return {"a": a, "b": b, "body": body}


# ---------------------------------------------------------------- 저장·목록·조회·갱신·삭제·소유자


def test_save_list_get_update_delete_and_only_the_owner_sees_it(kj, ab) -> None:  # noqa: F811
    u, other, manager = kj["kjj-user1"], kj["kjj-user2"], kj["owner"]
    before = _state()
    job = _job(u, "월간 교육 취합", ab["body"])
    assert job["version"] == 1 and job["input_count"] == 2 and job["key_columns"] == 1 and job["scope"] == "base"
    assert job["scope_label"] == "기준 파일의 key만" and job["key_names"] is None and job["aggregate"] is None
    r1, r2 = job["inputs"]
    assert r1["role"] == 1 and r1["is_base"] and r1["key_columns"] == [{"name": "사번", "letter": "A"}]
    assert r1["sheet"] == {"name": "A", "tab": 0} and r1["columns"] == [
        {"name": "부서", "letter": "B", "out_name": None}
    ]
    assert r2["role"] == 2 and not r2["is_base"] and r2["sheet"] == {"name": "교육", "tab": 0}
    assert r2["columns"] == [{"name": "교육 건수", "letter": "B", "out_name": "교육건수"}]
    assert r1["key_kinds"] == [{"text": 3, "int": 0}] and r2["key_kinds"] == [{"text": 3, "int": 0}]
    assert "file_id" not in r1 and "file_id" not in r2  # 파일 번호는 저장하지 않는다 (새 파일에 다시 맞춘다)
    assert [j["id"] for j in u.get("/keyjoin/jobs").json()] == [job["id"]]
    assert u.get(f"/keyjoin/jobs/{job['id']}").json()["name"] == "월간 교육 취합"
    # 다른 사용자·최고 관리자: 목록에 없고 조회·갱신·삭제·맞추기 모두 404 (있는지조차 알리지 않음)
    assert other.get("/keyjoin/jobs").json() == [] and manager.get("/keyjoin/jobs").json() == []
    for c in (other, manager):
        assert c.get(f"/keyjoin/jobs/{job['id']}").status_code == 404
        assert c.put(f"/keyjoin/jobs/{job['id']}", json={"expected_version": 1, "name": "x"}).status_code == 404
        assert c.delete(f"/keyjoin/jobs/{job['id']}").status_code == 404
        assert c.post(f"/keyjoin/jobs/{job['id']}/match", json={"file_ids": [ab["a"]]}).status_code == 404
    # 같은 이름 409 · 빈 이름 422 · 만들 수 없는 구성(한 입력 안의 key 중복)은 저장하지 않음 409
    dup = u.post("/keyjoin/jobs", json={"name": " 월간 교육 취합 ", "request": ab["body"]})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "KEYJOIN_JOB_NAME_TAKEN"
    assert u.post("/keyjoin/jobs", json={"name": "   ", "request": ab["body"]}).status_code == 422
    dup_keys = _csv(u, "dup.csv", [["사번", "값"], ["001", "1"], ["001", "2"]])
    bad = u.post("/keyjoin/jobs", json={"name": "중복", "request": _body([_input(ab["a"]), _input(dup_keys)])})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "KEYJOIN_JOB_NOT_READY"
    assert any("KEYJOIN_KEY_DUPLICATE" in p for p in bad.json()["detail"]["problems"])
    # 갱신: 이름만 → 버전 그대로, 설정 → 버전 +1
    renamed = u.put(f"/keyjoin/jobs/{job['id']}", json={"expected_version": 1, "name": "월간 교육"}).json()
    assert renamed["name"] == "월간 교육" and renamed["version"] == 1 and renamed["updated_at"] is not None
    body2 = _body([_input(ab["a"], cols=("c1",)), _input(ab["b"], cols=("c1",), names={"c1": "교육 수"})])
    changed = u.put(f"/keyjoin/jobs/{job['id']}", json={"expected_version": 1, "request": body2}).json()
    assert changed["version"] == 2 and changed["inputs"][1]["columns"][0]["out_name"] == "교육 수"
    assert u.put(f"/keyjoin/jobs/{job['id']}", json={"expected_version": 2}).status_code == 422  # 바꿀 것 없음
    assert u.delete(f"/keyjoin/jobs/{job['id']}").status_code == 204
    assert u.get(f"/keyjoin/jobs/{job['id']}").status_code == 404 and u.get("/keyjoin/jobs").json() == []
    assert _state() == before  # 저장·맞추기·삭제는 실행 기록·결과 파일을 만들지 않는다


def test_a_deleted_job_number_is_never_given_to_a_new_job(kj, ab) -> None:  # noqa: F811
    """다른 창에서 지우고 새로 저장한 작업이 옛 번호를 받으면 옛 화면의 삭제·갱신이 새 작업에 닿는다(2026-11-04
    격리 재현 — SQLite가 가장 큰 번호를 지운 뒤 그 번호를 다시 주던 조건). 번호를 다시 쓰지 않으므로(0024 — SQLite
    AUTOINCREMENT, PostgreSQL 시퀀스) 옛 번호의 삭제·갱신은 404이고 새 작업은 그대로다 — 같은 이름·같은 버전이어도,
    다른 계정이어도."""
    u, other = kj["kjj-user1"], kj["kjj-user2"]
    before = _state()
    old = _job(u, "번호 재사용 시험", ab["body"])  # 방금 만든 작업 = 지금 가장 큰 번호
    assert u.delete(f"/keyjoin/jobs/{old['id']}").status_code == 204
    again = _job(u, "번호 재사용 시험", ab["body"])  # 같은 소유자·같은 이름·같은 버전으로 다시 저장
    assert again["version"] == old["version"] == 1 and again["id"] != old["id"]
    assert u.delete(f"/keyjoin/jobs/{old['id']}").status_code == 404  # 옛 화면의 확인이 보낸 삭제
    stale_put = u.put(f"/keyjoin/jobs/{old['id']}", json={"expected_version": 1, "name": "덮기"})  # 옛 화면의 갱신
    assert stale_put.status_code == 404
    assert u.get(f"/keyjoin/jobs/{again['id']}").json()["name"] == "번호 재사용 시험"
    # 다른 계정이 그 뒤 새 작업을 저장해도 지운 번호를 받지 않고, 옛 번호의 삭제는 남의 작업에 닿지 않는다
    assert u.delete(f"/keyjoin/jobs/{again['id']}").status_code == 204
    a2 = _csv(other, "A2.csv", A_ROWS)
    b2 = _upload(other, "B2.xlsx", _xlsx_bytes({"교육": B_ROWS}))
    theirs = _job(other, "다른 계정 작업", _body([_input(a2, cols=("c1",)), _input(b2, cols=("c1",))]))
    assert theirs["id"] not in (old["id"], again["id"])
    assert u.delete(f"/keyjoin/jobs/{again['id']}").status_code == 404
    assert other.get(f"/keyjoin/jobs/{theirs['id']}").status_code == 200
    assert other.delete(f"/keyjoin/jobs/{theirs['id']}").status_code == 204
    assert u.get("/keyjoin/jobs").json() == [] and other.get("/keyjoin/jobs").json() == []
    assert _state() == before


# ---------------------------------------------------------------- 갱신의 버전 조건 (expected_version)


def _row(job_id: int) -> tuple[Any, ...]:
    """DB의 그 작업 행(이름·버전·설정·갱신 시각) — 앱과 같은 DB(SQLite·PostgreSQL)에서 읽는다."""
    rows = db_rows("select name, version, spec, updated_at from keyjoin_jobs where id = ?", job_id)
    return rows[0] if rows else ()


def _body2(ab: dict[str, Any], out_name: str) -> dict[str, Any]:
    return _body([_input(ab["a"], cols=("c1",)), _input(ab["b"], cols=("c1",), names={"c1": out_name})])


def test_update_requires_the_version_the_screen_saw(kj, ab) -> None:  # noqa: F811
    """갱신은 화면이 본 버전(expected_version)이 필수다 — 재발급의 expected_auth_version과 같은 계약. 없거나 정수가
    아니면 422, 지금 버전과 다르면 409(KEYJOIN_JOB_VERSION_CHANGED)이고 이름·설정·버전·갱신 시각 모두 그대로다.
    남의 작업·지운 작업은 버전이 맞아도 404."""
    u, other = kj["kjj-user1"], kj["kjj-user2"]
    before = _state()
    job = _job(u, "버전 조건", ab["body"])
    jid = job["id"]
    row0 = _row(jid)
    for bad in (
        {"request": _body2(ab, "x")},  # 버전 없음
        {"expected_version": None, "name": "x"},
        {"expected_version": True, "name": "x"},
        {"expected_version": "1", "name": "x"},
        {"expected_version": 1.0, "name": "x"},
        {"expected_version": 0, "name": "x"},
        {"expected_version": -1, "name": "x"},
        {"expected_version": 1, "name": "x", "x": 1},
    ):
        assert u.put(f"/keyjoin/jobs/{jid}", json=bad).status_code == 422, bad
    assert _row(jid) == row0
    # 맞는 버전: 설정 → v2
    v2 = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 1, "request": _body2(ab, "교육 수")})
    assert v2.status_code == 200 and v2.json()["version"] == 2
    row2 = _row(jid)
    # 옛 버전(v1)으로 설정·이름 갱신 → 409, 아무것도 바뀌지 않음
    for stale in ({"expected_version": 1, "request": _body2(ab, "덮기")}, {"expected_version": 1, "name": "덮기"}):
        res = u.put(f"/keyjoin/jobs/{jid}", json=stale)
        assert res.status_code == 409, res.text
        detail = res.json()["detail"]
        assert detail["code"] == "KEYJOIN_JOB_VERSION_CHANGED"
        assert "v1" in detail["message"] and "v2" in detail["message"]
        assert _row(jid) == row2
    # 앞선 버전(v3 — 아직 없음)도 409
    assert u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 3, "name": "덮기"}).status_code == 409
    # 이름만 바꾸면 버전은 그대로(v2), 조건에는 쓴다
    renamed = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 2, "name": "버전 조건 2"})
    assert renamed.status_code == 200 and renamed.json()["version"] == 2 and renamed.json()["name"] == "버전 조건 2"
    # 남의 작업은 버전이 맞아도 404, 행 그대로
    row3 = _row(jid)
    assert other.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 2, "name": "남의 것"}).status_code == 404
    assert _row(jid) == row3
    # 지운 작업은 404
    assert u.delete(f"/keyjoin/jobs/{jid}").status_code == 204
    assert u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 2, "name": "되살리기"}).status_code == 404
    assert _row(jid) == () and _state() == before


def test_two_windows_update_the_same_job(kj, ab) -> None:  # noqa: F811
    """두 창이 같은 작업(v1)을 열어 둔다 → 창 2가 먼저 갱신(v2) → 창 1의 갱신(v1 기준)은 409이고 창 2의 설정이 남는다.
    창 1이 최신(v2)을 다시 읽은 뒤 스스로 다시 누르면 그때만 v3 — 서버는 자동으로 덮지 않는다."""
    u = kj["kjj-user1"]
    job = _job(u, "두 창", ab["body"])
    jid = job["id"]
    seen1 = u.get(f"/keyjoin/jobs/{jid}").json()["version"]  # 창 1이 본 판
    seen2 = u.get(f"/keyjoin/jobs/{jid}").json()["version"]  # 창 2가 본 판
    assert seen1 == seen2 == 1
    w2 = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": seen2, "request": _body2(ab, "창2")})
    assert w2.status_code == 200 and w2.json()["version"] == 2
    after_w2 = _row(jid)
    w1 = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": seen1, "request": _body2(ab, "창1")})
    assert w1.status_code == 409 and w1.json()["detail"]["code"] == "KEYJOIN_JOB_VERSION_CHANGED"
    assert _row(jid) == after_w2
    latest = u.get(f"/keyjoin/jobs/{jid}").json()
    assert latest["version"] == 2 and latest["inputs"][1]["columns"][0]["out_name"] == "창2"
    again = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": latest["version"], "request": _body2(ab, "창1")})
    assert again.status_code == 200 and again.json()["version"] == 3
    assert again.json()["inputs"][1]["columns"][0]["out_name"] == "창1"
    assert u.delete(f"/keyjoin/jobs/{jid}").status_code == 204


def test_concurrent_updates_with_the_same_version_apply_once(kj, ab, monkeypatch) -> None:  # noqa: F811
    """같은 버전(v1)으로 두 갱신이 동시에 온다 — 둘 다 빠른 거절(버전 비교)과 설정 검사를 지난 뒤 조건부 UPDATE에서
    겹치게 한다(설정 검사 _spec_for가 끝난 직후에 둔 barrier, sleep에 기대지 않음). 설정 검사는 결과 만들기 차례
    (OUTPUT_SLOTS=1)를 쓰므로 시험 쪽에서 하나씩 돌린 뒤 barrier에 모은다. 요청마다 다른 스레드(각자의 세션).
    성공(200) 하나·409 하나, 최종 버전 2, 남은 설정 = 성공한 쪽. SQLite·PostgreSQL 모두 (실제 dialect는
    MOAJWO_RACE_RECORD에 기록 — 도착 때 풀의 연결 수는 설정 검사가 rollback으로 연결을 돌려준 뒤라 0일 수 있다)."""
    u = kj["kjj-user1"]
    job = _job(u, "동시 갱신", ab["body"])
    jid = job["id"]
    real = keyjoin_jobs._spec_for
    gate = threading.Barrier(2)
    seen: list[int | None] = []
    one_at_a_time = threading.Lock()

    def gated(*args: Any, **kw: Any) -> dict[str, Any]:
        with one_at_a_time:
            spec = real(*args, **kw)
            first = len(seen) < 2
            if first:
                seen.append(_checked_out())
        if first:
            gate.wait(timeout=20)  # 두 요청이 모두 설정 검사를 마친 뒤 함께 조건부 UPDATE로
        return spec

    monkeypatch.setattr(keyjoin_jobs, "_spec_for", gated)
    results: list[tuple[str, int, dict[str, Any]]] = []

    def attempt(label: str) -> None:
        res = u.put(f"/keyjoin/jobs/{jid}", json={"expected_version": 1, "request": _body2(ab, label)})
        results.append((label, res.status_code, res.json()))

    _run_threads([threading.Thread(target=attempt, args=(name,)) for name in ("동시A", "동시B")])
    codes = sorted(code for _, code, _ in results)
    assert codes == [200, 409], results
    winner = next(label for label, code, _ in results if code == 200)
    loser = next(body for _, code, body in results if code == 409)
    assert loser["detail"]["code"] == "KEYJOIN_JOB_VERSION_CHANGED"
    final = u.get(f"/keyjoin/jobs/{jid}").json()
    assert final["version"] == 2 and final["inputs"][1]["columns"][0]["out_name"] == winner
    _record("keyjoin_concurrent_update_same_version", {
        "requests": 2, "responses": codes, "final_version": final["version"], "winner_is_final": True,
        "gathered": len(seen), "pool_checked_out_at_arrival": seen,
    })  # fmt: skip
    assert u.delete(f"/keyjoin/jobs/{jid}").status_code == 204


# ---------------------------------------------------------------- 새 파일 맞추기


def test_match_new_files_with_changed_ids_names_and_column_order(kj, ab) -> None:  # noqa: F811
    u = kj["kjj-user1"]
    job = _job(u, "맞추기", ab["body"])
    # 다음 달 자료: 파일 번호·이름이 다르고(A-2월), B는 열 순서가 바뀌었으며(메모·교육 건수·사용자번호)
    # 시트 이름도 다르다
    a2 = _csv(u, "A-2월.csv", A_ROWS)
    b2_rows = [[r[2], r[1], r[0]] for r in B_ROWS]
    b2 = _upload(u, "B-2월.xlsx", _xlsx_bytes({"교육2": b2_rows}))
    before = _state()
    m = _match(u, job["id"], [b2, a2])  # 거꾸로 보내도 역할 순서(입력 1 = A)로 맞춘다
    assert m["ready"] and m["blocked"] == [] and m["unused_files"] == []
    req = m["request"]
    assert [i["file_id"] for i in req["inputs"]] == [a2, b2] and req["base"] == {"file_id": a2, "sheet_id": "s0"}
    assert req["inputs"][0] == {
        "file_id": a2,
        "sheet_id": "s0",
        "key_columns": ["c0"],
        "columns": [{"column_id": "c1", "out_name": None}],
        "rows": None,
        "aggregate": None,
    }
    assert req["inputs"][1]["key_columns"] == ["c2"]  # 이름이 같은 열을 새 위치에서 찾았다
    assert req["inputs"][1]["columns"] == [{"column_id": "c1", "out_name": "교육건수"}] and req["scope"] == "base"
    roles = m["roles"]
    assert [r["status"] for r in roles] == ["assigned", "assigned"]
    assert [c["status"] for c in roles[1]["columns"]] == [
        "moved",
        "same",
    ]  # key 열만 자리가 바뀌었다 (교육 건수는 그대로 B열)
    assert roles[1]["columns"][0]["note"] == "열 위치 변경: A열 → C열 (이름 같음)"
    assert sum("파일 이름이 저장 때와 다릅니다" in n for n in m["notes"]) == 2
    assert any("시트가 저장 때와 다릅니다" in n for n in m["notes"]) and any("열 위치 변경" in n for n in m["notes"])
    assert m["expected_key_kinds"] == {"1": [{"text": 3, "int": 0}], "2": [{"text": 3, "int": 0}]}
    assert _state() == before  # 맞추기는 읽기 전용
    # 그 요청 그대로 연결 확인 → 만들기: 새 파일로 만든 결과가 기대값과 같다
    run, wb = _make(u, req)
    assert [c.value for c in wb["연결 결과"][1]] == HEADER and _rows(wb) == EXPECTED
    assert run["summary"]["mode"] == "keyjoin" and run["input_file_count"] == 2
    assert _state()["runs"] == before["runs"] + 1


def test_match_asks_the_user_when_a_column_is_missing_ambiguous_or_dropped(kj, ab) -> None:  # noqa: F811
    u, other = kj["kjj-user1"], kj["kjj-user2"]
    job = _job(u, "열 확인", ab["body"])
    # A의 key 열 이름이 바뀜('직원번호') → key가 같은 이름으로 없어 후보가 아니다
    # (파일 이름이 같아도 자동으로 쓰지 않음)
    a3 = _csv(u, "A.csv", [["직원번호", *A_ROWS[0][1:]], *A_ROWS[1:]])
    m = _match(u, job["id"], [a3, ab["b"]])
    assert not m["ready"] and m["request"] is None
    r1, r2 = m["roles"]
    assert r1["status"] == "unassigned" and r1["file_id"] is None and r1["candidates"] == [] and r1["columns"] == []
    assert r2["status"] == "assigned" and r2["file_id"] == ab["b"]
    assert any("입력 1" in b and "파일을 고르세요" in b for b in m["blocked"])
    assert m["unused_files"] == [a3]
    # 사용자가 A.csv(바뀐 key 열 이름)를 고르면 key 열은 'missing' — 같은 위치의 열을 후보로만 보이고
    # 고르기 전에는 막는다
    picked = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": a3, "sheet_id": "s0", "columns": {}},
            {"role": 2, "file_id": ab["b"], "sheet_id": "s0", "columns": {}},
        ],
    )
    assert not picked["ready"]
    k = picked["roles"][0]["columns"][0]
    assert k["kind"] == "key" and k["status"] == "missing" and k["column_id"] is None
    assert k["candidates"] == [{"column_id": "c0", "name": "직원번호", "letter": "A"}]
    assert any("입력 1 key 1" in b for b in picked["blocked"])
    chosen = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": a3, "sheet_id": "s0", "columns": {"k0": "c0"}},
            {"role": 2, "file_id": ab["b"], "sheet_id": "s0", "columns": {}},
        ],
    )
    assert chosen["ready"] and chosen["request"]["inputs"][0]["key_columns"] == ["c0"]
    _run_ok, wb = _make(u, chosen["request"])
    assert _rows(wb) == EXPECTED
    # key 열은 뺄 수 없다
    dropped_key = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": a3, "sheet_id": "s0", "columns": {"k0": None}},
            {"role": 2, "file_id": ab["b"], "sheet_id": "s0", "columns": {}},
        ],
    )
    assert not dropped_key["ready"] and any("key 열은 뺄 수 없습니다" in b for b in dropped_key["blocked"])
    # B에 '교육 건수' 열이 둘 — 파서가 둘째를 '교육 건수_2'로 읽으므로 자동으로는 첫째(c1)가 맞고, 사용자는 p0로
    # 둘째(c2)를 고를 수 있다 (이름이 정말 같은 열은 resolve_column이 묻는다 — 아래 단위 시험)
    b4 = _upload(
        u,
        "B.xlsx",
        _xlsx_bytes({"교육": [["사용자번호", "교육 건수", "교육 건수"], ["001", "10", "99"], ["003", "30", "99"]]}),
    )
    m2 = _match(u, job["id"], [ab["a"], b4])
    assert m2["ready"] and m2["request"]["inputs"][1]["columns"] == [{"column_id": "c1", "out_name": "교육건수"}]
    picked2 = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": ab["a"], "sheet_id": "s0", "columns": {}},
            {"role": 2, "file_id": b4, "sheet_id": "s0", "columns": {"p0": "c2"}},
        ],
    )
    assert picked2["ready"] and picked2["request"]["inputs"][1]["columns"] == [
        {"column_id": "c2", "out_name": "교육건수"}
    ]
    # 가져올 열을 이번 작업에서 빼기 (null) — 요청에서 그 열만 빠진다
    dropped = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": ab["a"], "sheet_id": "s0", "columns": {}},
            {"role": 2, "file_id": b4, "sheet_id": "s0", "columns": {"p0": None}},
        ],
    )
    assert dropped["ready"] and dropped["request"]["inputs"][1]["columns"] == []
    # 형식 오류: 같은 파일을 두 역할에 · 역할 누락 · 남의 파일(없는 파일처럼) · 없는 열
    same = [
        {"role": 1, "file_id": ab["a"], "sheet_id": "s0", "columns": {}},
        {"role": 2, "file_id": ab["a"], "sheet_id": "s0", "columns": {}},
    ]
    assert u.post(f"/keyjoin/jobs/{job['id']}/apply", json={"assignments": same}).status_code == 422
    assert u.post(f"/keyjoin/jobs/{job['id']}/apply", json={"assignments": same[:1]}).status_code == 422
    theirs = _csv(other, "A.csv", A_ROWS)
    res = u.post(f"/keyjoin/jobs/{job['id']}/match", json={"file_ids": [theirs, ab["b"]]})
    assert res.status_code == 422 and str(theirs) in res.text and "찾을 수 없습니다" in res.text
    bad_col = [
        {"role": 1, "file_id": ab["a"], "sheet_id": "s0", "columns": {"p0": "c9"}},
        {"role": 2, "file_id": ab["b"], "sheet_id": "s0", "columns": {}},
    ]
    assert u.post(f"/keyjoin/jobs/{job['id']}/apply", json={"assignments": bad_col}).status_code == 422


def test_match_does_not_guess_between_files_with_the_same_score(kj, ab) -> None:  # noqa: F811
    u = kj["kjj-user1"]
    job = _job(u, "같은 점수", ab["body"])
    x = _csv(u, "X.csv", A_ROWS)  # 구조가 같고 이름은 둘 다 저장 때(A.csv)와 다르다 → 점수가 같다
    y = _csv(u, "Y.csv", A_ROWS)
    m = _match(u, job["id"], [x, y, ab["b"]])
    assert not m["ready"]
    r1 = m["roles"][0]
    assert r1["status"] == "ambiguous" and r1["file_id"] is None and [c["file_id"] for c in r1["candidates"]] == [x, y]
    assert len({c["score"] for c in r1["candidates"]}) == 1
    assert m["roles"][1]["status"] == "assigned" and m["roles"][1]["file_id"] == ab["b"]
    chosen = _apply(
        u,
        job["id"],
        [
            {"role": 1, "file_id": y, "sheet_id": "s0", "columns": {}},
            {"role": 2, "file_id": ab["b"], "sheet_id": "s0", "columns": {}},
        ],
    )
    assert chosen["ready"] and chosen["request"]["inputs"][0]["file_id"] == y and chosen["unused_files"] == []
    # 저장 때와 같은 이름의 파일이 있으면 그쪽이 앞선다 (보조 근거) — 단, key 열이 없는 파일은 이름이
    # 같아도 후보가 아니다
    m2 = _match(u, job["id"], [x, ab["a"], ab["b"]])
    assert m2["ready"] and m2["request"]["inputs"][0]["file_id"] == ab["a"] and m2["unused_files"] == [x]
    assert any("어느 입력에도 쓰지 않습니다" in n for n in m2["notes"])


def test_key_kind_change_is_reported_after_the_new_plan(kj, ab) -> None:  # noqa: F811
    u = kj["kjj-user1"]
    job = _job(u, "자료형", ab["body"])
    b_int = _upload(u, "B.xlsx", _xlsx_bytes({"교육": [B_ROWS[0], *[[int(r[0]), r[1], r[2]] for r in B_ROWS[1:]]]}))
    m = _match(u, job["id"], [ab["a"], b_int])
    assert m["ready"]
    plan = _plan(u, m["request"])
    assert plan["inputs"][1]["key_columns"][0]["kinds"]["int"] == 3
    spec = {"inputs": [{"role": r["role"], "key_kinds": r["key_kinds"]} for r in job["inputs"]]}
    notes = kind_changes(spec, plan)
    assert len(notes) == 1 and notes[0].startswith("입력 2 key 1: key 자료형이 저장 때(글자 3·정수 0)와 다릅니다")
    assert kind_changes(spec, _plan(u, _match(u, job["id"], [ab["a"], ab["b"]])["request"])) == []
    assert kind_changes({"inputs": [{"role": 1, "key_kinds": None}]}, plan) == []


def test_job_update_makes_the_old_basis_stale(kj, ab) -> None:  # noqa: F811
    u = kj["kjj-user1"]
    job = _job(u, "버전", ab["body"])
    req1 = _match(u, job["id"], [ab["a"], ab["b"]])["request"]
    basis1 = _plan(u, req1)["basis"]
    body2 = _body([_input(ab["a"], cols=("c1",)), _input(ab["b"], cols=("c1",), names={"c1": "교육 수"})])
    assert u.put(f"/keyjoin/jobs/{job['id']}", json={"expected_version": 1, "request": body2}).json()["version"] == 2
    m2 = _match(u, job["id"], [ab["a"], ab["b"]])
    assert m2["job"]["version"] == 2 and m2["request"]["inputs"][1]["columns"][0]["out_name"] == "교육 수"
    before = _state()
    stale = _run(u, m2["request"], basis1)  # 갱신 전 확인 결과로는 만들 수 없다
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "KEYJOIN_BASIS_CHANGED"
    assert _state() == before
    assert _run(u, m2["request"], _plan(u, m2["request"])["basis"]).status_code == 201


def test_mutated_copy_is_not_mistaken_for_the_saved_files(kj, ab) -> None:  # noqa: F811
    """값·행 순서를 바꾼 B의 변형 사본(시험용 — 실제 자료 아님)에 맞추면 결과는 새 값·새 원본 행 번호다."""
    u = kj["kjj-user1"]
    job = _job(u, "변형 사본", ab["body"])
    b_mut = _upload(u, "B.xlsx", _xlsx_bytes({"교육": B_MUTATED}))
    m = _match(u, job["id"], [ab["a"], b_mut])
    assert m["ready"] and m["notes"] == [] and m["request"]["inputs"][1]["file_id"] == b_mut
    _run_ok, wb = _make(u, m["request"])
    assert _rows(wb) == EXPECTED_MUTATED and _rows(wb) != EXPECTED
    assert _grid(wb["출처"])[1:] == EXPECTED_MUTATED_SOURCES


def test_resolve_column_asks_when_the_same_name_appears_twice_and_reports_moves() -> None:
    """파서는 같은 이름을 '이름_2'로 바꾸지만, 같은 이름이 둘인 시트 정보가 오면 자동으로 정하지 않는다(방어).
    이름이 같고 위치만 다른 열은 '열 위치 변경', 이름이 없는 열은 같은 위치의 열을 후보로만."""
    from app.bundle import SheetInfo
    from app.keyjoin_jobs import resolve_column

    sheet = SheetInfo(
        index=0, name="s", columns=3, header_row=1, two_level=False, kinds={}, formula_missing=0, formula_cached=0,
        long_numbers=0, column_names=["key", "값", "값"], source_columns=[0, 1, 2],
    )  # fmt: skip
    got = resolve_column(sheet, {"name": "값", "letter": "B", "index": 1, "out_name": None}, "pick", 0)
    assert got["status"] == "ambiguous" and got["column_id"] is None
    assert [c["column_id"] for c in got["candidates"]] == ["c1", "c2"] and "같은 이름의 열이 2개" in got["note"]
    same = resolve_column(sheet, {"name": "key", "letter": "A", "index": 0}, "key", 0)
    assert same["status"] == "same" and same["column_id"] == "c0" and same["note"] is None
    moved = resolve_column(sheet, {"name": "key", "letter": "B", "index": 1}, "key", 0)
    assert (
        moved["status"] == "moved"
        and moved["column_id"] == "c0"
        and moved["note"] == "열 위치 변경: B열 → A열 (이름 같음)"
    )
    missing = resolve_column(sheet, {"name": "없음", "letter": "C", "index": 2, "out_name": "x"}, "pick", 1)
    assert missing["status"] == "missing" and [c["column_id"] for c in missing["candidates"]] == ["c2"]
    assert missing["out_name"] == "x" and "빼세요" in missing["note"]
    beyond = resolve_column(sheet, {"name": "없음", "letter": "Z", "index": 25}, "key", 0)
    assert beyond["status"] == "missing" and beyond["candidates"] == [] and "고르세요" in beyond["note"]
