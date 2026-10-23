"""C-04: 기관·지역 용어사전 — 검사에 쓴 이름 해석과 결과에 쓸 해석이 같을 때만 그 검사 결과로 완료·통합한다.

재현 (수정 전 main adebb6c — 검사 기준에는 열 매핑·학습 별칭·표준 사전만 있고 용어사전은 없었다):
- 검토에서 '모아가청'을 용어사전 항목에 연결하지 않기로(거부) 하고 완료한 뒤 관리자가 별칭 '모아가청' → 모아가시험청을
  추가하면, 다시 검사 없이 통합되고 결과의 기관명이 '모아가시험청'(코드 1999101)으로 바뀌었다.
- 검사 때 용어사전으로 해석한 이름의 항목을 지우면, 결과에서 그 코드가 사라졌다 (필수 기관 검사는 옛 해석으로 통과).
- 검토에서 승인한 연결의 항목이 바뀌면(정식명칭 변경·삭제), 결과는 승인과 다른 이름으로 나갔다.

비교 범위: 검사에서 해석한 이름(데이터·전월 자료·필수 기관 목록)과 승인한 연결만 지금 용어사전으로 다시 해석한다
(파일 읽기·모델 호출 없음). 용어사전 지문이 검사 때와 같으면 다시 해석하지 않는다. 관련 없는 이름의 변경은 막지 않는다.
"""

import csv
import io
import itertools
import json
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

_users = itertools.count(1)
_codes = itertools.count(101)
_tags = iter("가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허")


@pytest.fixture
def n() -> dict[str, str]:
    """시험마다 다른 이름 (시험 DB를 함께 써서 같은 이름의 항목·별칭이 다른 시험에 남는다)."""
    t = next(_tags)
    return {"alias": f"모아{t}청", "entry": f"모아{t}시험청", "other": f"다른{t}시험청", "unrelated": f"다른{t}청"}


@pytest.fixture
def user(app_real_login, owner) -> TestClient:  # noqa: F811
    name = f"rg-user{next(_users)}"
    res = owner.post("/users", json={"username": name, "display_name": name.upper(), "status": "active",
                                     "password": f"pass-{name}"})  # fmt: skip
    assert res.status_code == 201, res.text
    return _client(app_real_login, name, f"pass-{name}")


def _entries(owner: TestClient, *names: str) -> list[str]:  # noqa: F811
    """시험마다 새 코드로 용어사전 항목을 만든다 (다른 시험의 항목과 섞이지 않게)."""
    codes = [f"1999{next(_codes):03d}" for _ in names]
    rows = "".join(f"{c},{n},중앙행정기관,,,\n" for c, n in zip(codes, names, strict=True))
    text = "코드,정식명칭,유형,시도코드,시군구코드,별칭\n" + rows
    res = owner.post("/glossary/import", files=[("file", ("g.csv", text.encode("utf-8"), "text/csv"))])
    assert res.status_code == 200 and res.json()["applied"], res.text
    return codes


def _alias(owner: TestClient, alias: str, code: str) -> int:  # noqa: F811
    res = owner.post("/glossary/aliases", json={"alias": alias, "target_code": code})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _entry_id(owner: TestClient, code: str) -> int:  # noqa: F811
    res = owner.get("/glossary/entries", params={"q": code})
    assert res.status_code == 200, res.text
    (entry,) = [e for e in res.json() if e["code"] == code]
    return entry["id"]


def _upload(c: TestClient, name: str, rows: list[list]) -> int:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    res = c.post("/files", files=[("files", (f"{name}.csv", buf.getvalue().encode("utf-8"), "text/csv"))])
    assert res.status_code == 200, res.text
    fid = res.json()[0]["id"]
    pv = c.get(f"/files/{fid}/preview").json()
    ids = {col["name"]: col["column_id"] for col in pv["columns"]}
    items = [{"sheet_id": pv["sheet_id"], "column_id": ids[n], "field_key": k} for n, k in MAP.items() if n in ids]
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
    return fid


MAP = {"기관명": "org_name", "집행액": None}


def _review(c: TestClient, fid: int, **params: Any) -> dict:
    res = c.post("/reviews", json={"file_ids": [fid], "expected_period": "2026-09", **params})
    assert res.status_code == 201, res.text
    return res.json()


def _entity(review: dict, name: str) -> dict:
    (item,) = [i for i in review["items"] if i["kind"] == "entity" and i["before"] == name]
    return item


def _decide(c: TestClient, review: dict, item: dict, decision: str, choice: str | None = None) -> None:
    res = c.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json={"decision": decision, "choice": choice})
    assert res.status_code == 200, res.text


def _complete(c: TestClient, review: dict) -> None:
    res = c.post(f"/reviews/{review['id']}/complete")
    pending = [(i["kind"], i["before"], i["reason"]) for i in review["items"] if i["category"] in ("block", "pending")]
    assert res.status_code == 200, (res.text, pending)


def _state() -> dict[str, Any]:
    """통합 실행 기록·변경 내역·결과 파일 (거절된 요청이 아무것도 만들지 않았는지)."""
    from app.db import SessionLocal
    from app.integration import OUTPUT_DIR
    from app.models import IntegrationRun, RunChange

    with SessionLocal() as db:
        runs = db.scalar(select(func.count(IntegrationRun.id)))
        changes = db.scalar(select(func.count(RunChange.id)))
    return {"runs": runs, "changes": changes, "files": sorted(p.name for p in OUTPUT_DIR.glob("*"))}


def _rejected(c: TestClient, review: dict, reason: str) -> None:
    """다시 검사가 필요하다고 알리고, 통합은 아무것도 만들지 않고 거절한다."""
    got = c.get(f"/reviews/{review['id']}").json()
    assert got["needs_recheck"] is True and any(reason in r for r in got["recheck_reasons"]), got["recheck_reasons"]
    before = _state()
    res = c.post(f"/reviews/{review['id']}/integrate", json={"include_unmapped": True})
    assert res.status_code == 409 and reason in res.json()["detail"], res.text
    assert _state() == before


def _output(c: TestClient, run_id: int) -> list[dict[str, Any]]:
    ws = openpyxl.load_workbook(io.BytesIO(c.get(f"/runs/{run_id}/download").content)).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def _integrate(c: TestClient, review: dict) -> list[dict[str, Any]]:
    res = c.post(f"/reviews/{review['id']}/integrate", json={"include_unmapped": True})
    assert res.status_code == 201, res.text
    return _output(c, res.json()["id"])


def _rows(n: dict[str, str]) -> list[list[str]]:
    return [["기관명", "집행액"], [n["alias"], "100"], [n["entry"], "200"]]


def test_alias_added_after_check_needs_recheck_before_output(user, owner, n) -> None:  # noqa: F811
    (code,) = _entries(owner, n["entry"])
    fid = _upload(user, "별칭추가", _rows(n))
    review = _review(user, fid)
    _decide(user, review, _entity(review, n["alias"]), "rejected")  # 용어사전 항목에 연결하지 않음
    _complete(user, review)
    assert user.get(f"/reviews/{review['id']}").json()["needs_recheck"] is False

    _alias(owner, n["alias"], code)  # 검사·결정 뒤 관리자가 별칭을 더함
    _rejected(user, review, f"'{n['alias']}': 검사 때 용어사전에 없음 → 지금 {n['entry']}({code})")

    # 다시 검사하면 지금 용어사전으로 해석해 확인 필요 없이 연결된다
    new = user.post(f"/reviews/{review['id']}/recheck", json={"replace": {}}).json()
    assert not [i for i in new["items"] if i["kind"] == "entity" and i["category"] == "pending"]
    _complete(user, new)
    rows = _integrate(user, new)
    assert [(r["기관명"], r["기관명 코드"]) for r in rows] == [(n["entry"], code), (n["entry"], code)]


def test_entry_removed_after_check_needs_recheck(user, owner, n) -> None:  # noqa: F811
    (code,) = _entries(owner, n["entry"])
    fid = _upload(user, "항목삭제", [["기관명", "집행액"], [n["entry"], "100"]])
    review = _review(user, fid, required_entities=[n["entry"]])
    assert not [i for i in review["items"] if i["category"] in ("block", "pending")]  # 필수 기관 검사 통과
    _complete(user, review)

    assert owner.delete(f"/glossary/entries/{_entry_id(owner, code)}").status_code == 204
    _rejected(user, review, f"'{n['entry']}': 검사 때 {n['entry']}({code}) → 지금 용어사전에 없음")


def test_approved_link_whose_entry_changed_needs_recheck(user, owner, n) -> None:  # noqa: F811
    (code,) = _entries(owner, n["entry"])
    fid = _upload(user, "승인연결", _rows(n))
    review = _review(user, fid)
    item = _entity(review, n["alias"])
    assert code in [c["code"] for c in item["payload"]["candidates"]]
    _decide(user, review, item, "approved", code)
    _complete(user, review)

    # 같은 코드의 정식명칭을 바꾼다 (가져오기는 코드가 같으면 갱신)
    renamed = n["entry"] + "본부"
    text = f"코드,정식명칭,유형,시도코드,시군구코드,별칭\n{code},{renamed},중앙행정기관,,,\n"
    res = owner.post("/glossary/import", files=[("file", ("g.csv", text.encode("utf-8"), "text/csv"))])
    assert res.status_code == 200 and res.json()["applied"], res.text
    _rejected(user, review, f"'{n['alias']}': 검토에서 승인한 연결 {n['entry']}({code}) → 지금 {renamed}({code})")


def test_unrelated_glossary_change_keeps_the_check(user, owner, n) -> None:  # noqa: F811
    code, other = _entries(owner, n["entry"], n["other"])
    fid = _upload(user, "관련없음", [["기관명", "집행액"], [n["entry"], "100"]])
    review = _review(user, fid)
    _complete(user, review)

    _alias(owner, n["unrelated"], other)  # 이 자료에 없는 이름
    got = user.get(f"/reviews/{review['id']}").json()
    assert (got["needs_recheck"], got["recheck_reasons"]) == (False, [])
    out = _integrate(user, review)
    assert [(r["기관명"], r["기관명 코드"]) for r in out] == [(n["entry"], code)]


def test_glossary_change_during_integration_uses_the_checked_glossary(user, owner, n, monkeypatch) -> None:  # noqa: F811
    """통합이 용어사전을 읽어 비교한 뒤 다른 요청이 바꿔도 이 실행은 비교한 용어사전으로 만든다 — 다음 요청은 거절."""
    from app import integration
    from app.db import SessionLocal
    from app.glossary import normalize_name
    from app.models import GlossaryAlias

    code, other = _entries(owner, n["entry"], n["other"])
    fid = _upload(user, "도중변경", _rows(n))
    review = _review(user, fid)
    _decide(user, review, _entity(review, n["alias"]), "rejected")
    _complete(user, review)
    original, changed = integration.load_parsed, []

    def change_then_load(db, file_id):  # 검사 기준 비교 다음, 파일을 읽는 순간에 끼어든 다른 요청
        if not changed:
            with SessionLocal() as other_db:
                alias = GlossaryAlias(alias=n["alias"], normalized=normalize_name(n["alias"]), target_code=other,
                                      source="user")  # fmt: skip
                other_db.add(alias)
                other_db.commit()
            changed.append(file_id)
        return original(db, file_id)

    monkeypatch.setattr(integration, "load_parsed", change_then_load)
    rows = _integrate(user, review)
    assert changed == [fid]
    assert [(r["기관명"], r["기관명 코드"]) for r in rows] == [(n["alias"], None), (n["entry"], code)]
    _rejected(user, review, f"'{n['alias']}': 검사 때 용어사전에 없음 → 지금 {n['other']}({other})")


def test_review_without_an_entity_record_is_not_treated_as_unchanged(user, owner, n) -> None:  # noqa: F811
    from app.db import SessionLocal
    from app.models import ReviewSession

    _entries(owner, n["entry"])
    fid = _upload(user, "기록없음", _rows(n))
    review = _review(user, fid)
    _decide(user, review, _entity(review, n["alias"]), "rejected")
    _complete(user, review)
    with SessionLocal() as db:  # 이 보완 전 버전에서 만든 검토 (검사 기준에 용어사전 해석이 없음)
        session = db.get(ReviewSession, review["id"])
        params = json.loads(session.params)
        params["check_basis"].pop("entities")
        session.params = json.dumps(params, ensure_ascii=False)
        db.commit()
    _rejected(user, review, "기관·지역 용어사전 해석을 기록하기 전 버전")


def test_link_learned_on_complete_keeps_the_review_ready(user, owner, n) -> None:  # noqa: F811
    """'이 형식 기억하기'로 완료하면 승인한 연결이 용어사전 별칭으로 학습된다 — 그 검토의 결정 그대로라 다시 검사가
    필요하지 않다 (같은 이름을 '연결하지 않음'으로 검사한 다른 사용자의 검토는 다시 검사가 필요하다)."""
    (code,) = _entries(owner, n["entry"])
    rejected = _review(user, _upload(user, "다른검토", _rows(n)))
    _decide(user, rejected, _entity(rejected, n["alias"]), "rejected")

    review = _review(owner, _upload(owner, "기억하기", _rows(n)))  # 이름 학습은 관리자 검토만
    _decide(owner, review, _entity(review, n["alias"]), "approved", code)
    assert owner.patch(f"/reviews/{review['id']}", json={"remember_format": True}).status_code == 200
    done = owner.post(f"/reviews/{review['id']}/complete").json()
    assert done["learned"]["names"] == 1 and done["summary"].startswith("검토 완료"), done
    got = owner.get(f"/reviews/{review['id']}").json()
    assert (got["needs_recheck"], got["recheck_reasons"]) == (False, [])
    rows = _integrate(owner, review)
    assert [(r["기관명"], r["기관명 코드"]) for r in rows] == [(n["entry"], code), (n["entry"], code)]

    other = user.get(f"/reviews/{rejected['id']}").json()
    assert other["needs_recheck"] is True
    assert f"'{n['alias']}': 검사 때 용어사전에 없음 → 지금 {n['entry']}({code})" in other["recheck_reasons"][0]


def test_recheck_needed_refuses_completion_and_keeps_earlier_results(user, owner, n) -> None:  # noqa: F811
    """'다시 검사 필요' (2026-10-23 정책): 다시 검사하기 전에는 완료·통합을 거절하고 기록·결과 파일을 만들지 않는다.
    이 검토로 이미 만든 결과는 그대로 조회·다운로드한다 (결과를 지우거나 다시 만들지 않음)."""
    (code,) = _entries(owner, n["entry"])
    done = _review(user, _upload(user, "결과유지", _rows(n)))
    _decide(user, done, _entity(done, n["alias"]), "rejected")
    _complete(user, done)
    res = user.post(f"/reviews/{done['id']}/integrate", json={"include_unmapped": True})
    assert res.status_code == 201, res.text
    run_id = res.json()["id"]
    earlier = (user.get(f"/runs/{run_id}").json(), user.get(f"/runs/{run_id}/download").content)
    waiting = _review(user, _upload(user, "완료전", _rows(n)))  # 같은 이름을 '연결하지 않음'으로 결정만 하고 완료 전
    _decide(user, waiting, _entity(waiting, n["alias"]), "rejected")

    _alias(owner, n["alias"], code)  # 검사 뒤 관리자가 별칭을 더함 → 두 검토 모두 다시 검사 필요
    _rejected(user, done, f"'{n['alias']}': 검사 때 용어사전에 없음")
    before = _state()
    res = user.post(f"/reviews/{waiting['id']}/complete")
    assert res.status_code == 409 and "다시 검사 필요" in res.json()["detail"], res.text
    assert user.get(f"/reviews/{waiting['id']}").json()["status"] == "in_review" and _state() == before

    after = (user.get(f"/runs/{run_id}").json(), user.get(f"/runs/{run_id}/download").content)
    assert after == earlier  # 이미 만든 결과는 그대로 조회·다운로드된다
