"""열 매핑 작업 화면: 엑셀형 미리보기, 저장된 분석, 여러 파일 공통 검토, 선택 일괄 확정 (실제 쿠키 로그인)."""

import io
import threading
from typing import Any

import openpyxl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.conftest import db_connect
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(rows: list[list], merges: tuple[str, ...] = (), title: str | None = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "현황"
    if title:
        ws.append([title])
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(c: TestClient, name: str, data: bytes) -> int:
    res = c.post("/files", files=[("files", (name, data, XLSX))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _analyze(c: TestClient, fid: int) -> dict[str, Any]:
    res = c.post(f"/files/{fid}/mapping", params={"use_llm": "false"})
    assert res.status_code == 200, res.text
    return res.json()


def _col(mapping: dict, name: str, sheet: int = 0) -> dict:
    return next(c for c in mapping["sheets"][sheet]["columns"] if c["column"] == name)


def _db(sql: str, *args: Any) -> list[tuple]:
    with db_connect() as con:
        return con.execute(sql, args).fetchall()


@pytest.fixture(scope="module")
def users(app_real_login, owner) -> dict[str, TestClient]:  # noqa: F811
    from app import mapping_review
    from app.mapping import MappingEngine, load_dictionary

    engine = MappingEngine(
        load_dictionary(), None, lambda s, u, schema: {"key": "none", "confidence": 0, "reason": "-"}
    )
    patch = pytest.MonkeyPatch()
    patch.setattr(mapping_review, "get_engine", lambda: engine)
    made = {"owner": owner}
    for name in ("mw-user1", "mw-user2"):
        res = owner.post("/users", json={"username": name, "display_name": name.upper(), "status": "active",
                                         "password": "pass-" + name})  # fmt: skip
        assert res.status_code == 201, res.text
        made[name] = _client(app_real_login, name, "pass-" + name)
    yield made
    patch.undo()


# ---------------------------------------------------------------- 미리보기


def test_preview_letters_duplicates_paging_and_permissions(users) -> None:
    u1, u2, boss = users["mw-user1"], users["mw-user2"], users["owner"]
    # 머리글 가로 병합(자가용 B1:C1)으로 생긴 복제 열(C)은 빠지고, 같은 이름 '합계' 두 열은 column_id로 구분
    rows = [["지역", "자가용", None, "합계", "합계", "긴 열 이름 " + "가" * 80, None]]
    rows += [[f"지역{i}", i, i, i * 2, i * 3, None if i % 7 == 0 else i, None] for i in range(1, 121)]
    fid = _upload(u1, "미리보기.xlsx", _xlsx(rows, ("B1:C1",)))
    res = u1.get(f"/files/{fid}/preview", params={"limit": 30})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "파싱 후 정리된 값" in body["basis"]
    names = [c["name"] for c in body["columns"]]
    ids = [c["column_id"] for c in body["columns"]]
    assert len(set(ids)) == len(ids) and names.count("합계") == 1 and "합계_2" in names
    letters = {c["name"]: c["letter"] for c in body["columns"]}
    assert (letters["지역"], letters["자가용"], letters["합계"], letters["합계_2"]) == ("A", "B", "D", "E")
    assert body["dropped_columns"] == 1
    assert len(body["rows"]) == 30 and body["total_rows"] == 120 and body["rows"][0]["row"] == 2
    page = u1.get(f"/files/{fid}/preview", params={"offset": 100, "limit": 50}).json()
    assert len(page["rows"]) == 20 and page["rows"][0]["row"] == 102
    assert u1.get(f"/files/{fid}/preview", params={"limit": 1000}).status_code == 422  # 한 번에 최대 200행
    assert u1.get(f"/files/{fid}/preview", params={"sheet_id": "s9"}).status_code == 404  # 시트는 sheet_id로
    assert u2.get(f"/files/{fid}/preview").status_code == 404  # 다른 일반 사용자
    assert boss.get(f"/files/{fid}/preview").status_code == 200  # 최고 관리자는 조회 가능
    # 2단 머리글의 연도 열은 각각 남고, 머리글 행 두 줄이 그대로 내려온다
    years = _upload(
        u1, "연도.xlsx", _xlsx([["지역", "인구", None], ["지역", 2024, 2025], ["서울", 1, 2]], ("A1:A2", "B1:C1"))
    )
    yb = u1.get(f"/files/{years}/preview").json()
    assert [c["name"] for c in yb["columns"]] == ["지역", "인구_2024", "인구_2025"]
    assert len(yb["header_rows"]) == 2 and yb["header_rows"][1][1:] == [2024, 2025]


def test_preview_masks_headers_cells_and_numeric_rrn(users, caplog) -> None:
    import logging

    u1 = users["mw-user1"]
    raw = ("alice@example.com", "010-1234-5678", "1234-5678", "900101-1234567", "9001011234567")
    rows = [["기관명", "alice@example.com", "담당 연락처", "주민번호"],
            ["행정안전부", "x", "010-1234-5678", "900101-1234567"],
            ["기상청", "y", "010-2345-6789", 9001011234567]]  # fmt: skip
    fid = _upload(u1, "개인정보미리보기.xlsx", _xlsx(rows))
    caplog.set_level(logging.DEBUG)
    text = u1.get(f"/files/{fid}/preview").text
    assert not any(r in text for r in raw), text
    mapping = _analyze(u1, fid)
    latest = u1.get(f"/files/{fid}/mapping/latest").text
    for blob in (str(mapping), latest, caplog.text):
        assert not any(r in blob for r in raw)


# ---------------------------------------------------------------- 저장된 분석


def test_latest_mapping_is_stored_and_merges_decisions(users) -> None:
    u1, boss = users["mw-user1"], users["owner"]
    fid = _upload(u1, "저장분석.xlsx", _xlsx([["기관명", "집행액", "세부 메모"], ["행정안전부", 1, "a"]]))
    before = u1.get(f"/files/{fid}/mapping/latest").json()  # 분석 전: 자동으로 분석하지 않고 '미분석'으로 보여 줌
    assert before["analyzed"] is False and {c["status"] for c in before["sheets"][0]["columns"]} == {"unanalyzed"}
    assert _db("select count(*) from mapping_analyses where source_file_id = ?", fid) == [(0,)]
    first = _analyze(u1, fid)
    assert first["analyzed_at"]
    memo = _col(first, "세부 메모")
    decision = {"sheet": "현황", "column_id": memo["column_id"], "field_key": "note"}
    res = u1.put(f"/files/{fid}/mapping/decisions", json={"decisions": [decision]})
    assert res.status_code == 200
    latest = u1.get(f"/files/{fid}/mapping/latest").json()
    col = _col(latest, "세부 메모")
    assert (col["status"], col["method"], col["field_key"]) == ("confirmed", "user", "note")
    assert latest["can_change"] is True and latest["stale"] is False
    assert len(_db("select id from mapping_analyses where source_file_id = ?", fid)) == 1
    _analyze(u1, fid)  # 다시 분석해도 1건 (바꿔 씀)
    assert len(_db("select id from mapping_analyses where source_file_id = ?", fid)) == 1
    # 최고 관리자: 자기 분석 기록이 없으면 '미분석'(확정값은 보임), 분석해도 조회 전용
    boss_view = boss.get(f"/files/{fid}/mapping/latest").json()
    assert boss_view["analyzed"] is False and boss_view["can_change"] is False
    assert _col(boss_view, "세부 메모")["status"] == "confirmed"
    _analyze(boss, fid)
    assert boss.get(f"/files/{fid}/mapping/latest").json()["can_change"] is False


# ---------------------------------------------------------------- 공통 검토


def _common_files(u: TestClient) -> list[int]:
    a = _upload(u, "공통A.xlsx", _xlsx(
        [["기관명", "기관코드", "관리번호", "합계", "인구_2024", "등록대수", "세부 메모"],
         ["행정안전부", "1741000", "A-001", 10, 100, 5, "x"], ["기상청", "1360000", "A-002", 20, 200, 6, "y"]],
        title="자동차 등록 현황 (단위: 대)"))  # fmt: skip
    b = _upload(u, "공통B.xlsx", _xlsx(
        [["기관명", "기관코드", "발급기번호", "합계", "인구_2025", "등록대수", "세부 메모"],
         ["행정안전부", "1741000", "0001", 10, 100, 5, "x"], ["인사혁신처", "1760000", "0002", 30, 300, 7, "z"]],
        title="신규 등록 실적 (단위: 천대)"))  # fmt: skip
    return [a, b]


def test_common_review_groups_without_reanalysis(users, monkeypatch) -> None:
    from app.mapping import MappingEngine

    u1 = users["mw-user1"]
    a, b = _common_files(u1)
    nothing = _upload(u1, "분석안함.xlsx", _xlsx([["기관명"], ["행정안전부"]]))
    for fid in (a, b):
        _analyze(u1, fid)
    before = _db("select count(*) from mapping_analyses")[0][0]

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("공통 검토가 매핑 분석(모델 호출)을 실행함")

    monkeypatch.setattr(MappingEngine, "map_columns", boom)
    res = u1.post("/mapping/common", json={"file_ids": [a, b, nothing]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert _db("select count(*) from mapping_analyses")[0][0] == before  # 분석 기록을 새로 만들지 않음
    assert body["missing"] == [{"file_id": nothing, "name": "분석안함.xlsx"}]
    groups = {g["name"]: g for g in body["groups"]}
    assert groups["기관명"]["category"] == "common" and groups["기관명"]["field_key"] == "org_name"
    assert groups["기관명"]["files"] == 2 and len(groups["기관명"]["members"]) == 2
    # 같은 이름이라도: 합계(대상·단위 불명), 단위가 다른 표(대 / 천대) → 공통으로 묶지 않음
    assert groups["합계"]["category"] == "divergent"
    assert any("이름만으로는" in r for r in groups["합계"]["reasons"])
    assert groups["기관명"]["reasons"] == []  # 시트 단위(대/천대)는 숫자 열에만 적용 — 글자 열은 그대로 공통
    assert groups["등록대수"]["category"] == "divergent" and any("단위" in r for r in groups["등록대수"]["reasons"])
    # 다른 연도 열·용도가 다른 식별자는 서로 묶이지 않음
    assert groups["인구_2024"]["category"] in ("only", "unresolved") and groups["인구_2024"]["files"] == 1
    assert groups["관리번호"]["files"] == 1 and groups["발급기번호"]["files"] == 1
    assert "관리번호" not in [m["column"] for m in groups["기관코드"]["members"]]
    assert u1.post("/mapping/common", json={"file_ids": []}).status_code == 422


def test_common_review_rejects_unviewable_files(users) -> None:
    u1, u2 = users["mw-user1"], users["mw-user2"]
    mine = _upload(u1, "내파일.xlsx", _xlsx([["기관명"], ["행정안전부"]]))
    other = _upload(u2, "남의파일.xlsx", _xlsx([["기관명"], ["기상청"]]))
    assert u1.post("/mapping/common", json={"file_ids": [mine, other]}).status_code == 404


# ---------------------------------------------------------------- 일괄 확정


def _two_files_with_memo(u: TestClient, tag: str) -> tuple[int, int, str, str]:
    rows = [["기관명", f"{tag} 메모"], ["행정안전부", "a"], ["기상청", "b"]]
    a, b = _upload(u, f"{tag}A.xlsx", _xlsx(rows)), _upload(u, f"{tag}B.xlsx", _xlsx(rows))
    ca = _col(_analyze(u, a), f"{tag} 메모")["column_id"]
    cb = _col(_analyze(u, b), f"{tag} 메모")["column_id"]
    return a, b, ca, cb


def _item(fid: int, cid: str, key: str | None, **extra: Any) -> dict[str, Any]:
    return {"file_id": fid, "sheet_id": "s0", "column_id": cid, "field_key": key, **extra}


def _decisions(fid: int) -> list[tuple]:
    return _db("select column_name, field_key from mapping_decisions where source_file_id = ? order by 1", fid)


def test_batch_preview_and_apply_with_overwrite_and_idempotency(users) -> None:
    u1 = users["mw-user1"]
    a, b, ca, cb = _two_files_with_memo(u1, "일괄")
    # b의 메모는 이미 다른 값으로 확정
    u1.put(
        f"/files/{b}/mapping/decisions",
        json={"decisions": [{"sheet": "현황", "column_id": cb, "field_key": "department"}]},
    )
    plan = u1.post("/mapping/batch/preview", json={"items": [_item(a, ca, "note"), _item(b, cb, "note")]}).json()
    acts = {(p["file_id"], p["action"]) for p in plan["items"]}
    assert acts == {(a, "apply"), (b, "overwrite")} and plan["selected"] == 2 and plan["applicable"] == 1
    assert plan["items"][1]["current_field_key"] == "department" and plan["items"][1]["current_confirmed"] is True
    assert _decisions(a) == []  # 미리보기는 저장하지 않음

    expected = [_item(a, ca, "note", expected_confirmed=False, expected_field_key=None),
                _item(b, cb, "note", expected_confirmed=True, expected_field_key="department")]  # fmt: skip
    res = u1.post("/mapping/batch/apply", json={"items": expected})
    assert res.status_code == 409 and "덮어쓰기" in str(res.json()["detail"])  # 덮어쓰기를 고르지 않음 → 전부 미반영
    assert _decisions(a) == [] and _decisions(b) == [("일괄 메모", "department")]
    expected[1]["overwrite"] = True
    res = u1.post("/mapping/batch/apply", json={"items": expected})
    assert res.status_code == 200 and res.json()["applied"] == 2, res.text
    assert _decisions(a) == [("일괄 메모", "note")] and _decisions(b) == [("일괄 메모", "note")]
    # 같은 요청을 다시 보내도 중복 저장·충돌 없음 (이미 같은 값)
    again = u1.post("/mapping/batch/apply", json={"items": expected})
    assert again.status_code == 200 and again.json() == {"applied": 0, "unchanged": 2, "learned": 0, "files": 0}
    assert len(_db("select id from mapping_decisions where source_file_id in (?, ?)", a, b)) == 2
    # 같은 사용자가 여러 파일에서 승인해도 승격 근거는 1명
    uid = _db("select id from users where username = 'mw-user1'")[0][0]
    approvals = (
        "select count(*) from mapping_approvals where normalized = '일괄메모' and field_key = 'note' and user_id = ?"
    )
    assert _db(approvals, uid) == [(1,)]  # (먼저 '부서'로 확정한 승인 1건은 다른 매핑이라 따로 남음)
    assert _db("select count(*) from learned_aliases where normalized = '일괄메모' and user_id = ?", uid) == [(1,)]


def test_batch_apply_is_all_or_nothing(users) -> None:
    u1, u2 = users["mw-user1"], users["mw-user2"]
    a, b, ca, cb = _two_files_with_memo(u1, "원자")
    others = _upload(u2, "남의원자.xlsx", _xlsx([["기관명", "원자 메모"], ["행정안전부", "a"]]))
    co = _col(_analyze(u2, others), "원자 메모")["column_id"]
    ok = dict(expected_confirmed=False, expected_field_key=None)
    # 남의 파일이 섞이면 403 (전부 미반영)
    res = u1.post("/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok), _item(others, co, "note", **ok)]})
    assert res.status_code in (403, 404) and _decisions(a) == [] and _decisions(others) == []
    # 없는 표준 항목·없는 열이 하나라도 있으면 409 (전부 미반영)
    res = u1.post(
        "/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok), _item(b, cb, "no_such_field", **ok)]}
    )
    assert res.status_code == 409 and _decisions(a) == [] and _decisions(b) == []
    res = u1.post("/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok), _item(b, "c99", "note", **ok)]})
    assert res.status_code == 409 and _decisions(a) == []
    # 확인한 뒤 다른 요청이 확정값을 바꾸면 충돌 (조용히 덮어쓰지 않음)
    u1.put(
        f"/files/{b}/mapping/decisions",
        json={"decisions": [{"sheet": "현황", "column_id": cb, "field_key": "department"}]},
    )
    res = u1.post("/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok), _item(b, cb, "note", **ok)]})
    assert res.status_code == 409 and "바뀌었습니다" in str(res.json()["detail"])
    assert _decisions(a) == [] and _decisions(b) == [("원자 메모", "department")]
    # 같은 열 두 번 → 422
    dup = u1.post("/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok), _item(a, ca, "note", **ok)]})
    assert dup.status_code == 422
    # 최고 관리자도 남의 파일은 일괄 확정 불가 (조회 전용)
    boss = users["owner"]
    assert boss.post("/mapping/batch/apply", json={"items": [_item(a, ca, "note", **ok)]}).status_code == 403
    plan = boss.post("/mapping/batch/preview", json={"items": [_item(a, ca, "note")]}).json()
    assert plan["items"][0]["action"] == "excluded" and "조회 전용" in plan["items"][0]["reason"]


def test_concurrent_batch_applies_one_wins(users, tmp_path) -> None:
    """같은 열을 서로 다른 값으로 동시에 일괄 확정: 한 요청만 반영되고,
    다른 요청은 충돌(409)로 아무것도 저장하지 않는다."""
    from app import mapping_review
    from app.auth import CurrentUser
    from app.db import SessionLocal

    u1 = users["mw-user1"]
    a, _, ca, _ = _two_files_with_memo(u1, "동시")
    uid = _db("select id from users where username = 'mw-user1'")[0][0]
    me = CurrentUser(uid, "mw-user1", "MW-USER1", "user")
    barrier = threading.Barrier(2)
    outcomes: list[Any] = []

    def attempt(key: str) -> None:
        item = mapping_review.ApplyItem(
            file_id=a, sheet_id="s0", column_id=ca, field_key=key, expected_confirmed=False, expected_field_key=None
        )
        body = mapping_review.ApplyIn(items=[item])
        with SessionLocal() as db:
            barrier.wait(timeout=10)
            try:
                outcomes.append(mapping_review.batch_apply(body, db, me).applied)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt, args=(k,)) for k in ("note", "department")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(map(str, outcomes)) == ["1", "409"], outcomes
    assert len(_decisions(a)) == 1


def test_common_members_match_single_file_results(users) -> None:
    """공통 검토의 각 열 상태·매핑은 같은 파일의 단일 파일 화면(저장된 분석 + 확정값)과 같다."""
    u1 = users["mw-user1"]
    rows = [
        ["기관명", "일관성 확인 메모"],
        ["행정안전부", "a"],
        ["기상청", "b"],
    ]  # 이 시험에만 쓰는 열 이름 (학습 별칭 없음)
    a, b = _upload(u1, "일관성A.xlsx", _xlsx(rows)), _upload(u1, "일관성B.xlsx", _xlsx(rows))
    for fid in (a, b):
        _analyze(u1, fid)
    memo = _col(u1.get(f"/files/{a}/mapping/latest").json(), "일관성 확인 메모")
    decision = {"sheet": "현황", "column_id": memo["column_id"], "field_key": "note"}
    assert u1.put(f"/files/{a}/mapping/decisions", json={"decisions": [decision]}).status_code == 200
    review = u1.post("/mapping/common", json={"file_ids": [a, b]}).json()
    single = {fid: u1.get(f"/files/{fid}/mapping/latest").json() for fid in (a, b)}
    for g in review["groups"]:
        for m in g["members"]:
            col = next(c for c in single[m["file_id"]]["sheets"][0]["columns"] if c["column_id"] == m["column_id"])
            assert (m["column"], m["status"], m["field_key"]) == (col["column"], col["status"], col["field_key"])
    memo_group = next(g for g in review["groups"] if g["name"] == "일관성 확인 메모")
    assert memo_group["category"] == "divergent"  # 한 파일은 비고로 확정, 다른 파일은 제안 없음 → 판단이 다름
    assert any("파일별 매핑이 다름" in r for r in memo_group["reasons"])


def test_preview_view_cache_reuses_masked_parse_and_stays_small(users, monkeypatch) -> None:
    """쪽 넘김은 같은 파일을 다시 파싱하지 않고(파일 id·sha256 키),
    보관한 값도 마스킹돼 있으며, 최근 2개 파일만 둔다."""
    from app import mapping_review

    u1 = users["mw-user1"]
    rows = [["기관명", "연락처"]] + [[f"기관{i}", "010-1234-5678"] for i in range(300)]
    fid = _upload(u1, "캐시.xlsx", _xlsx(rows))
    calls: list[int] = []
    real = mapping_review.load_parsed

    def counting(db, file_id):  # noqa: ANN001
        calls.append(file_id)
        return real(db, file_id)

    monkeypatch.setattr(mapping_review, "load_parsed", counting)
    first = u1.get(f"/files/{fid}/preview", params={"limit": 100}).json()
    second = u1.get(f"/files/{fid}/preview", params={"offset": 100, "limit": 100}).json()
    assert calls.count(fid) == 1 and second["rows"][0]["row"] == 102 and first["total_rows"] == 300
    cached = next(v for k, v in mapping_review._VIEWS.items() if k[0] == fid)
    assert "010-1234-5678" not in repr(cached.sheets[0].rows)  # 보관한 값도 마스킹
    for i in range(3):
        u1.get(f"/files/{_upload(u1, f'캐시{i}.xlsx', _xlsx([['기관명'], ['행정안전부']]))}/preview")
    assert len(mapping_review._VIEWS) <= mapping_review.VIEW_CACHE_FILES


def test_concurrent_previews_parse_once(users, monkeypatch) -> None:
    """큰 파일을 처음 열 때 미리보기·저장된 분석 요청이 동시에 와도 같은 파일은 한 번만 파싱한다."""
    from app import mapping_review
    from app.auth import CurrentUser
    from app.db import SessionLocal

    u1 = users["mw-user1"]
    fid = _upload(u1, "동시미리보기.xlsx", _xlsx([["기관명", "집행액"]] + [[f"기관{i}", i] for i in range(50)]))
    uid = _db("select id from users where username = 'mw-user1'")[0][0]
    me = CurrentUser(uid, "mw-user1", "MW-USER1", "user")
    calls: list[int] = []
    real = mapping_review.load_parsed
    gate = threading.Barrier(4)

    def slow(db, file_id):  # noqa: ANN001
        calls.append(file_id)
        return real(db, file_id)

    monkeypatch.setattr(mapping_review, "load_parsed", slow)
    totals: list[int] = []

    def attempt() -> None:
        with SessionLocal() as db:
            gate.wait(timeout=10)
            totals.append(mapping_review.preview(fid, db, me, None, 0, 10).total_rows)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert totals == [50, 50, 50, 50] and calls.count(fid) == 1, calls
