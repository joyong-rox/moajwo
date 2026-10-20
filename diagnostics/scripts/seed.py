"""진단 격리 스택에 합성 시험 자료를 만든다 (실제 개인정보·운영 자료 없음). 사용법: python seed.py

계정: diag-owner(최고 관리자, 최초 설정) · diag-user1·2·3(일반) · diag-pending(가입 신청, 승인 대기)
자료(사용자별): 일반 통계 표, 긴 파일명·긴 열 이름·같은 표시 이름, 여러 시트, 합성 개인정보 형식(example.com·010-0000),
수식·HTML·SQL처럼 보이는 글자, 120개 지역(여러 쪽). 흐름: 확정 → 검토 → (결정) → 완료 → 통합, 분석, 파일별 묶기.
만든 번호는 증거 폴더 seed-ids.json에 남긴다.
"""

import json
import sys

from diag_common import EV, USERS, Api, creds, xlsx

LOG: list[str] = []


def note(msg: str) -> None:
    LOG.append(msg)
    print(msg, flush=True)


def must(res, ok=(200, 201, 204), what=""):
    s, b, _ = res
    if s not in ok:
        note(f"   [실패] {what}: {s} {str(b)[:200]}")
        raise SystemExit(1)
    return b


POP = "주민등록 인구 현황 2026년 9월 (단위: 명)"
SIDO = ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시", "세종특별자치시",
        "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도"]  # fmt: skip
LONG_FILE = "아주_긴_파일_이름_" + "가나다라마바사아자차카타파하" * 12 + "_2026년9월_인구현황.xlsx"
LONG_COL = "주민등록 인구수 (" + "세부 구분 설명이 매우 긴 열 이름 " * 6 + ")"
SPECIAL = ["=1+1", "@SUM(A1:A2)", "+cmd|' /C calc'!A0", "<img src=x onerror=alert(1)>", "<script>alert('x')</script>",
           "'; DROP TABLE users;--", "../../etc/passwd", "기관A"]  # fmt: skip


def setup_accounts(c: dict[str, str]) -> dict[str, Api]:
    anon = Api()
    s, b, _ = anon.call("GET", "/auth/status")
    if b.get("setup_required"):
        must(anon.call("POST", "/auth/setup", {"username": USERS["owner"], "display_name": "진단 관리자", "password": c["owner"]}),
             what="최초 설정")  # fmt: skip
        note("최초 관리자 diag-owner 생성")
    owner = Api().login("owner")
    for key in ("user1", "user2", "user3"):
        s, b, _ = owner.call("POST", "/users", {"username": USERS[key], "display_name": f"진단 사용자{key[-1]}", "status": "active",
                                                "password": c[key]})  # fmt: skip
        note(f"사용자 {USERS[key]}: {s}")
    s, b, _ = Api().call("POST", "/auth/signup", {"username": "diag-pending", "display_name": "가입 대기자", "password": c["pending"],
                                                  "password_confirm": c["pending"]})  # fmt: skip
    note(f"가입 신청 diag-pending: {s}")
    return {"owner": owner, **{k: Api().login(k) for k in ("user1", "user2", "user3")}}


def upload(api: Api, name: str, content: bytes, ctype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") -> int:
    b = must(api.upload(name, content, ctype), what=f"업로드 {name[:30]}")
    return b[0]["id"]


def confirm(api: Api, fid: int, mapping: dict[str, str | None], sheet_id: str = "s0") -> None:
    pv = must(api.call("GET", f"/files/{fid}/preview?sheet_id={sheet_id}"), what=f"미리보기 {fid}")
    cols = {x["name"]: x["column_id"] for x in pv["columns"]}
    items = [{"sheet_id": sheet_id, "column_id": cols[n], "field_key": k} for n, k in mapping.items()]
    must(api.call("PUT", f"/files/{fid}/mapping/decisions", {"decisions": items}), what=f"확정 {fid}")


def review_flow(api: Api, fids: list[int], who: str, ids: dict) -> None:
    s, rv, _ = api.call("POST", "/reviews", {"file_ids": fids, "expected_period": "2026-09"})
    note(f"{who} 검토 생성: {s} {rv.get('status') if isinstance(rv, dict) else str(rv)[:120]}")
    if s != 201:
        return
    ids[f"{who}_review"] = rv["id"]
    for it in rv["items"]:
        if it["category"] == "pending" and it["can_change"] and it["decision"] is None:
            choice = (it["payload"].get("candidates") or [{}])[0].get("key") if isinstance(it["payload"], dict) else None
            s2, _, _ = api.call("POST", f"/reviews/{rv['id']}/items/{it['id']}/decision", {"decision": "approved", "choice": choice})
            note(f"   항목 {it['id']} {it['kind']} 승인: {s2}")
    s, done, _ = api.call("POST", f"/reviews/{rv['id']}/complete")
    note(f"{who} 검토 완료: {s} {str(done.get('status') if isinstance(done, dict) else done)[:120]}")
    if s == 200:
        s, run, _ = api.call("POST", f"/reviews/{rv['id']}/integrate", {})
        note(f"{who} 통합: {s}")
        if s == 201:
            ids[f"{who}_run"] = run["id"]


def analysis_flow(api: Api, fid: int, who: str, ids: dict) -> None:
    body = {"field_key": "population", "targets": [{"file_id": fid}], "group_by": ["sido"],
            "bins": {"on": "sum", "ranges": [{"label": "작은 지역", "max": 5000}, {"label": "큰 지역", "min": 5000}]}}  # fmt: skip
    s, pv, _ = api.call("POST", "/analysis/preview", body)
    if s != 200:
        note(f"{who} 분석 확인: {s} {str(pv)[:160]}")
        return
    s, r, _ = api.call("POST", "/analysis/run", {**body, "expected_fingerprint": pv["fingerprint"]})
    note(f"{who} 분석 실행: {s}")
    if s == 201:
        ids[f"{who}_analysis"] = r["id"]


def bundle_flow(api: Api, fids: list[int], who: str, ids: dict) -> None:
    body = {"file_ids": fids, "summary": {"items": [{"field_key": "population", "aggregates": ["sum"], "links": []}]}}
    s, plan, _ = api.call("POST", "/bundle/plan", body)
    note(f"{who} 묶기 구성: {s} {str(plan)[:160] if s != 200 else ''}")
    if s != 200:
        return
    s, run, _ = api.call("POST", "/bundle/run", {**body, "expected_basis": plan["basis"]})
    note(f"{who} 묶기 생성: {s} {str(run)[:160] if s not in (200, 201) else ''}")
    if s in (200, 201) and isinstance(run, dict):
        ids[f"{who}_bundle_run"] = run.get("id") or run.get("run_id")


def main() -> None:
    c = creds()
    apis = setup_accounts(c)
    ids: dict = {}
    head = [[POP], ["시도", "인구수"]]
    for who in ("user1", "user2"):
        api = apis[who]
        base = 1000 if who == "user1" else 2000
        f_basic = upload(api, f"{who}_인구_시도별.xlsx", xlsx({"현황": [*head, *[[s, base + i * 10] for i, s in enumerate(SIDO)]]}))
        confirm(api, f_basic, {"시도": "sido", "인구수": "population"})
        f_many = upload(api, f"{who}_지역120.xlsx", xlsx({"현황": [[POP], ["기관명", "인구수"], *[[f"지역기관{i:03d}", i * 7] for i in range(1, 121)]]}))
        confirm(api, f_many, {"기관명": "org_name", "인구수": "population"})
        ids[who] = {"basic": f_basic, "many": f_many}
        if who == "user1":
            f_long = upload(api, LONG_FILE, xlsx({"현황": [[POP], ["시도", LONG_COL, "비고"], *[[s, 10 + i, "같은 표시"] for i, s in enumerate(SIDO[:5])]]}))
            f_multi = upload(api, "여러시트_인구.xlsx", xlsx({
                "현황A": [*head, ["서울특별시", 100], ["부산광역시", 200]],
                "현황B": [*head, ["대구광역시", 300], ["인천광역시", 400]],
                "참고": [["항목", "내용"], ["작성", "합성 자료"]],
            }))  # fmt: skip
            confirm(api, f_multi, {"시도": "sido", "인구수": "population"}, "s0")
            confirm(api, f_multi, {"시도": "sido", "인구수": "population"}, "s1")
            pii_csv = "이름,연락처,이메일,인구수\n홍길동,010-0000-0001,hong@example.com,10\n김합성,010-0000-0002,kim@example.com,20\n".encode("utf-8-sig")
            f_pii = upload(api, "합성_개인정보_형식.csv", pii_csv, "text/csv")
            f_special = upload(api, "특수값_<b>굵게</b>_합성.xlsx", xlsx({"현황": [[POP], ["기관명", "인구수", "<i>열</i>"],
                                                                         *[[v, i + 1, v] for i, v in enumerate(SPECIAL)]]}))  # fmt: skip
            confirm(api, f_special, {"기관명": "org_name", "인구수": "population"})
            ids[who] |= {"long": f_long, "multi": f_multi, "pii": f_pii, "special": f_special}
        review_flow(api, [f_basic], who, ids)
        analysis_flow(api, f_basic, who, ids)
        many = {"field_key": "population", "targets": [{"file_id": f_many}], "group_by": ["org_name"]}
        s, pv, _ = api.call("POST", "/analysis/preview", many)
        if s == 200:
            s, r, _ = api.call("POST", "/analysis/run", {**many, "expected_fingerprint": pv["fingerprint"]})
            ids[f"{who}_analysis_many"] = r.get("id") if s == 201 else None
            note(f"{who} 분석(120그룹): {s}")
        bundle_flow(api, [f_basic, f_many], who, ids)
    (EV / "seed-ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    (EV / "seed-log.txt").write_text("\n".join(LOG), encoding="utf-8")
    print(json.dumps(ids, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
