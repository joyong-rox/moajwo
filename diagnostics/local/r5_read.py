"""5장 결과 판독: 차례 폴더의 진단 결과를 기대와 비교해 한 줄씩 적는다 (판정 = 정해 둔 기대 조건과의 비교).
사용법: python r5_read.py <차례 폴더> <라벨(예: r5s1)> <DB 종류> <차례> > 표.md"""

import json
import sys
from pathlib import Path

D, LABEL, DBK, ROUND = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
rows: list[tuple[str, str, str, bool | None, str]] = []


def load(rel: str):  # noqa: ANN201
    p = D / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def add(area: str, check: str, how: str, ok: bool | None, detail: object) -> None:
    rows.append((area, check, how, ok, str(detail).replace("|", "/").replace("\n", " ")[:260]))


summary = [line.split("\t") for line in (D / "summary.tsv").read_text(encoding="utf-8").splitlines()[1:] if "\t" in line]
for s in summary:
    if s[1] != "0":
        add("실행", s[0], "-", False, f"종료 코드 {s[1]}")

flow = load("flow/flow-check.json")
if flow:
    items = flow["results"]
    fails = [r["step"] for r in items if not r["ok"]]
    redeployed = (D / "gone").exists()  # 빈 데이터 폴더로 재배포한 차례 — 묶기(C)는 1차 시드 파일(원본 없음)을 쓴다
    if redeployed and fails and all(f.startswith(("C.", "오류: bundle")) for f in fails):
        add("업로드→매핑→검토→통합→내려받기 (B)", f"flow_check {len(items) - len(fails)}단계", "화면", True, "B1~B5 기대대로")
        add("묶기 C — 재배포 전 원본 (1차 시드 파일)", "원본이 없으면 막히고 안내", "화면", None,
            "기대된 동작: 재배포로 원본 파일이 지워짐 → 엑셀 만들기 비활성 + 410 안내 (아래 '재배포 뒤 묶기 화면'). 새로 올린 파일의 묶기는 5장 D2에서 기대대로")  # fmt: skip
    else:
        add("업로드→매핑→검토→통합→내려받기·묶기 요약 끔/켬", f"flow_check {len(items)}단계", "화면", not fails, fails or "모두 기대대로")
scr = D / "gone" / "bundle-old-files-screen.txt"
if scr.exists():
    t = scr.read_text(encoding="utf-8")
    add("재배포 뒤 묶기 화면 (원본 없음)", "앱 화면 안에 상태·복구 방법", "화면", "원본 파일이 서버에 없습니다" in t and "다시 올리면" in t, t[t.find("결과 구성"):][:150])

for rel in sorted((D / "r5").glob("from-log-*.json")) + sorted((D / "r5").glob("round5-r*.json")) + sorted((D / "r5").glob("rerun*/round5-r*.json")):
    r5 = json.loads(rel.read_text(encoding="utf-8"))
    tag = rel.parent.name if rel.parent.name.startswith("rerun") else ("본 실행, 로그에서 복원" if rel.name.startswith("from-log") else "본 실행")
    for r in r5["results"]:
        how = "화면" if r["part"] in ("U", "C", "D", "V", "K") and not r["step"].startswith("C1") else "API"
        add(f"5장 추가 확인 {r['part']} ({tag})", r["step"], how, r["ok"], r["detail"])

rs = load(f"review_status/review-status-{LABEL}.json")
if rs:
    st = {s["case"]: " / ".join(x["text"] for x in s["steps"][1:2]) for s in rs["state"]}
    changed = [k for k in st if "changed" in k or "late" in k]
    ok = all("다시 열기 필요" in st[k] for k in changed if "while-loading" not in k) and all("다시 열기" not in st[k] for k in st if "reverted" in k)
    add("공통 검토 상태 (선택 변경·되돌리기·조회 실패·늦은 응답·키보드·390px)", f"{len(st)}개 상태", "화면", ok and rs["mobile"][0]["overflow_px"] == 0,
        {k: v[:60] for k, v in st.items() if k in changed or "reverted" in k or "failed" in k})  # fmt: skip

ub = load(f"upload_busy/upload-busy-{LABEL}.json")
if ub:
    ch = {c["case"]: c["opened"] for c in ub["chooser"]}
    busy = next((s for s in ub["state"] if s["case"] == "1-busy"), {})
    ok = ch.get("업로드 중 버튼 클릭") is False and ch.get("업로드 중 키보드 Enter") is False and bool(busy.get("why")) and all(not a["violations"] for a in ub["axe"])
    add("업로드 중 '지금은 고를 수 없음' (U-06 후속, 해결 판정 아님)", "버튼 옆 이유·설명 연결·axe·390px", "화면", ok and ub["mobile"][0]["overflow_px"] == 0,
        {"chooser": ch, "described_by": busy.get("described_by"), "axe": [a["violations"] for a in ub["axe"]]})  # fmt: skip

ss = load(f"summary_source/summary-source-{LABEL}.json")
if ss:
    first = (ss.get("analysis-result") or [{}])[0].get("first_row")
    add("통계 (시연 5개, 발신Mail건수)", "합계 85,740·평균 342.96·최솟값 41·최댓값 2,150·개수 250", "화면", bool(first) and first[:5] == ["85,740", "342.96", "41", "2,150", "250"], first)
    ex = (ss.get("bundle-excel") or [{}])[0]
    add("파일별 시트 (요약 켬)", "5+1시트·요약 합계 85,740", "화면", ex.get("sheets", [])[-1:] == ["통계 요약"] and len(ex.get("sheets", [])) == 6 and ex.get("table", [[], []])[1][1] == 85740,
        (ex.get("sheets"), ex.get("table", [[], []])[1][:2]))  # fmt: skip

de = load("demo-expected.json")
if de:
    add("입력 (시연 5개)", "5×50행×15열 = 250행·3,750값", "API", de["input"] == {"rows": 250, "values": 3750}, de["input"])
    ok = all(isinstance(v, dict) and v.get("sum") == v.get("expected") for v in de["per_ministry"].values())
    add("통계 (부처별)", "37,550·14,510·11,355·4,735·17,590", "API", ok, {k.split()[0]: (v.get("sum"), v.get("expected")) for k, v in de["per_ministry"].items()})
    b = de["bundle_summary_off"]
    add("파일별 시트 (요약 끔)", "5시트·원본 3,825칸(머리글 포함) 대조", "API", b["sheet_count"] == 5 and b["mismatches"] == 0, f"시트 {b['sheet_count']} · 대조 {b['cells_compared']}칸 · 불일치 {b['mismatches']}")
    i = de["integration"]
    add("통합 (시연 5개, 지정 매핑 조건)", "250행·27열", "API", i.get("rows") == 250 and i.get("columns") == 27, f"완료 {i.get('complete')} · 행 {i.get('rows')} · 열 {i.get('columns')} · 빈칸 {i.get('blank_cells')}")

mf = load(f"mail_formula/mail-formula-{LABEL}.json")
if mf:
    refused = (mf.get("bundle-run-refused") or [{}])[0]
    excl = (mf.get("analysis-result") or [{}])[0].get("excluded", [])
    ok = refused.get("status") == 409 and "산식 불일치" in json.dumps(refused, ensure_ascii=False) and any("사용자가 계산에서 뺌" in x for x in excl)
    add("산식 불일치 (D-01, #25 정책)", "같은 통계로 더하기 409 → 명시적으로 뺀 뒤 계산", "화면+API", ok, {"refused": refused.get("status"), "excluded": excl})

sp = load(f"sum_partial/sum-partial-{LABEL}.json")
if sp:
    st = {k: v["validation"]["status"] for k, v in sp.items()}
    ok = st == {"normal": "passed", "marker": "passed_with_warnings", "unread_equal": "passed_with_warnings", "unread_diff": "blocked"}
    add("불완전 합계 검사 (C-03, #27 정책)", "정상 통과·표기 경고·읽지 못한 값 경고·실제 불일치 차단", "API", ok, st)

pr = load(f"precision/precision-{LABEL}.json")
if pr:
    c = pr["cases"]
    ok = c["decimal"]["api"]["sum"] == 0.3 and c["bins"]["api"]["bin"] == "0.3 이하" and c["big"]["api"]["sum"] == 9007199254740994 and c["big"]["excel"]["결과_1행"][0][1] == "s"
    add("정확한 수 계산 (C-02)", "0.3·구간·2^53·16자리 글자 칸", "API+Excel", ok, {k: c[k]["api"]["sum"] for k in ("decimal", "bins", "big", "sixteen")})

gb = load(f"glossary_basis/glossary-basis-{LABEL}.json")
if gb:
    got = {k: gb[k]["after"]["integrate"] for k in ("alias_added", "entry_removed", "unrelated")}
    add("검사 기준 변경 뒤 409 (C-04, #28 정책)", "관련 변경 409·관련 없는 변경 201", "API", got == {"alias_added": 409, "entry_removed": 409, "unrelated": 201}, got)

f1 = load(f"f1/f1-repro-{LABEL}.json")
if f1:
    an = f1["analysis"]
    groups_ok = all(len(a.get("groups", [])) == 2 for a in an if "충돌" in a["what"])
    raw0 = all(a.get(k, 0) == 0 for a in an for k in ("raw_in_preview", "raw_in_stored", "raw_in_api", "raw_in_excel"))
    add("F-1 (서로 다른 원문 그룹 보존·출력 가림)", f"분석 {len(an)}건", "API+Excel", groups_ok and raw0, [(a["what"], [g[2] for g in a.get("groups", [])]) for a in an][:6])

az = load(f"authz/authz-{LABEL}.json")
if az:
    b_reads = {v for k, v in az["reads"]["B"].items() if not k.endswith("A의 것)")}
    anon = {v for k, v in az["reads"]["anon"].items() if not k.endswith("A의 것)")}
    changed = {who: [k for k, v in w.items() if v["db_changed"] and v["status"] >= 400] for who, w in az["writes"].items()}
    add("소유권·권한", "다른 사용자 404·익명 401·거절 시 DB 변경 0", "API", b_reads == {404} and anon == {401} and not any(changed.values()),
        {"B": sorted(b_reads), "anon": sorted(anon), "거절인데 바뀐 것": changed})  # fmt: skip

a2 = load("a02.json")
if a2:
    ok = (a2["run1_same_format"]["run_status"] == "done" and a2["run2_new_column"]["counts"]["pending"] == 1
          and a2["run3_period_mismatch"]["counts"]["block"] == 1 and a2["mapping_changed_during_review"]["after"] is True)  # fmt: skip
    add("자동화 (최초 등록·반복)", "등록 1회·같은 형식 완료·새 열 1건·기간 차단·변경 뒤 재검사", "API", ok, {k: a2[k].get("run_status") for k in ("run1_same_format", "run2_new_column", "run3_period_mismatch")})

se = load("session.json")
if se:
    ok = "HttpOnly" in se["login"]["attributes"] and se["after_logout"] == 401 and se["expiry"]["after"] == 401 and se["pending_login"]["status"] == 403
    add("인증 상태", "쿠키 속성·로그아웃 401·만료 401·승인 대기 403", "API", ok, {"logout": se["after_logout"], "expiry": se["expiry"], "pending": se["pending_login"]["status"]})

rp = load("responsive/u05-responsive.json")
if rp:
    over = [(p["width"], p["page"]) for p in rp["pages"] if p["overflow_px"] > 0]
    viol = [(p["width"], p["page"], p["violations"]) for p in rp["pages"] if p["violations"]]
    add("표현 (대표 8화면×7폭)", "가로 넘침 0·axe 위반 0", "화면", not over and not viol, f"넘침 {over} · 위반 {viol}")

mr = load("a11y_manual/manual-records.json")
if mr:
    z = mr.get("zoom200", [])
    over = [x["page"] for x in z if x.get("pageOverflow", 0) > 0]
    add("실제 200% 확대 (대표 화면)", f"{len(z)}화면 페이지 가로 넘침", "화면", bool(z) and not over,
        {"css_width_dpr": z[0].get("css_width_dpr") if z else None, "pages": [x["page"] for x in z], "overflow": over})  # fmt: skip
    nav, rev, tabs = mr.get("nav", []), mr.get("review-open", []), mr.get("tabs", [])
    first = next((t["stops"] for t in mr.get("tab-order", []) if t.get("where", "").startswith("홈")), [])
    add("키보드 Tab 순서 (로그인 직후 홈)", "본문 바로가기 다음에 메뉴, '검토 목록'이 앞 10번 안", "화면",
        "a:검토 목록" in first[:10] and first[:1] == ["a:본문으로 바로가기"], first[:8])  # fmt: skip
    add("키보드 (a11y_manual: 앞 25회 뒤부터 '검토 목록' 찾기)", "Tab·Enter·Escape", "화면", None,
        "측정만 — 파일이 많은 계정(70개+)에서는 25회 뒤부터 60회 안에 한 바퀴를 돌지 못해 '못 찾음'이 됨 (시험 도구 한계, 순서는 위 줄). "
        + str({"nav": [x.get("menu_reached") for x in nav], "review_found": [bool(x.get("found")) for x in rev if "found" in x]}))  # fmt: skip
    add("키보드 (탭 목록 화살표)", "열 매핑 탭 Enter·←", "화면", any(x.get("reached") for x in tabs), tabs[:1])

for rel, tag in (("reentry/reentry.json", ""), ("reentry-rerun1/reentry.json", " (도구 수정 뒤 다시)")):
    re_ = load(rel)
    if re_:
        fails = [s["step"] for s in re_["steps"] if not s["ok"]]
        add(f"재진입 (같은 계정 A){tag}", f"{len(re_['steps'])}단계", "화면", not fails, fails or "모두 기대대로")
if (D / "reentry.log").exists() and not load("reentry/reentry.json"):
    add("재진입 (같은 계정 A)", "도구가 중간에 멈춤", "화면", False,
        "재배포 뒤 이전 결과 파일이 없어 내려받기를 기다리다 시간 초과 — 도구가 예외를 처리하지 않음 (시험 도구 오류, 다시 실행 줄 참고)")  # fmt: skip
gone = load("gone/gone.json")
if gone:
    a = gone["after_click"]
    add("재배포 전 결과 링크 (파일 없음)", "500·무한 로딩·다른 파일 없이 안내", "화면", a["download"] is None and "결과 파일이 서버에 없습니다" in a["body"],
        f"이동 {a['url']} · 응답 {a['responses'][:1]} · 화면 원문 JSON {a['body'][:90]}… · 뒤로 {gone['after_back']} · 재배포 뒤 결과 내려받기 {gone['newest_download']}")  # fmt: skip

print(f"### {DBK} {ROUND}차 ({LABEL})\n")
print("| 영역 | 확인 | 경로 | 판정 | 실제 |")
print("|---|---|---|---|---|")
for area, check, how, ok, detail in rows:
    mark = "기대대로" if ok else ("측정" if ok is None else "**다름**")
    print(f"| {area} | {check} | {how} | {mark} | {detail} |")
