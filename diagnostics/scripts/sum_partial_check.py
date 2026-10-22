"""C-03 확인: 합계 검사가 일부 하위 값만 읽었거나 검사를 건너뛴 경우, 검증·사전 검토가 그 사실을 보여 주는지
(격리 서버, 새 계정, 합성 자료, 측정만 저장 — 판정하지 않음).

사례 (시도 '계' 행과 시군구 행, 인구·세대수):
- normal: 정상 (합계 = 하위 행 합)
- marker: 세대수 하위 값에 비공개 'x' — 합계 검사를 건너뛰는 경우
- unread_equal: 인구 하위 값 하나가 '12명'(읽을 수 없는 글자) — 읽은 값만 더하면 합계와 같아지는 경우
  (그 값까지 더하면 312로 합계 300과 다름)
- unread_diff: 인구 하위 값 하나가 '12명'이고 읽은 값의 합도 합계와 다른 경우
홈 '검증 실행'(POST /validation)의 상태·차단·경고·참고와, 사전 검토(POST /reviews)의 요약·점수·항목 종류를 적는다.
사용법: python sum_partial_check.py <출력 폴더> <라벨> <새 계정 이름>
"""

import json
import secrets
import sys
from pathlib import Path

from diag_common import XLSX, Api, xlsx

OUT, LABEL, NAME = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)
TITLE = ["2026년 9월 서울특별시 시군구별 인구 현황 (단위: 명, 세대)"]
HEAD = ["시도", "시군구", "인구", "세대수"]
CASES = {
    "normal": [["서울특별시", "계", 300, 30], ["서울특별시", "종로구", 100, 10], ["서울특별시", "중구", 200, 20]],
    "marker": [["서울특별시", "계", 300, 30], ["서울특별시", "종로구", 100, "x"], ["서울특별시", "중구", 200, 20]],
    "unread_equal": [["서울특별시", "계", 300, 30], ["서울특별시", "종로구", 100, 10], ["서울특별시", "중구", 200, 20],
                     ["서울특별시", "용산구", "12명", 0]],
    "unread_diff": [["서울특별시", "계", 350, 30], ["서울특별시", "종로구", 100, 10], ["서울특별시", "중구", 200, 20],
                    ["서울특별시", "용산구", "12명", 0]],
}  # fmt: skip


def main() -> None:
    owner = Api().login("owner")
    password = "Sp-" + secrets.token_urlsafe(14) + "7z"
    s, b, _ = owner.call("POST", "/users", {"username": NAME, "display_name": f"합계 확인 {LABEL}", "status": "active", "password": password})
    if s != 201:
        raise RuntimeError(f"계정 생성 실패: {s} {b}")
    u = Api()
    if u.call("POST", "/auth/login", {"username": NAME, "password": password})[0] != 200:
        raise RuntimeError("새 계정 로그인 실패")
    rec: dict[str, dict] = {}
    for case, rows in CASES.items():
        s, body, _ = u.upload(f"합계_{case}.xlsx", xlsx({"현황": [TITLE, HEAD, *rows]}), XLSX)
        fid = body[0]["id"]
        s, v, _ = u.call("POST", "/validation", {"file_ids": [fid]})
        s2, r, _ = u.call("POST", "/reviews", {"file_ids": [fid]})
        rec[case] = {
            "file": fid,
            "validation": {"status": v.get("status"), "blocks": [(x["code"], x["message"]) for x in v.get("blocks", [])],
                           "warnings": [(x["code"], x["message"]) for x in v.get("warnings", [])], "notes": v.get("notes")},
            "review": {"status": s2, "summary": r.get("summary"), "kinds": sorted({(i["category"], i["kind"]) for i in r.get("items", [])}),
                       "block_reasons": [i["reason"] for i in r.get("items", []) if i["category"] == "block"],
                       "warning_reasons": [i["reason"] for i in r.get("items", []) if i["category"] == "warning"]},
        }  # fmt: skip
        print(case, json.dumps(rec[case], ensure_ascii=False, default=str)[:900], flush=True)
    (OUT / f"sum-partial-{LABEL}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
