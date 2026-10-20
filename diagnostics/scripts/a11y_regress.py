"""접근성 회귀 판정 — a11y_fix_check.py가 저장한 측정값(JSON)을 읽어 D-01~D-07과 확인창 보완이 다시 깨졌는지 판정한다.

- 이 스크립트는 화면에 접속하지 않는다 (측정은 a11y_fix_check.py, 판정은 여기 — 둘을 나눈다).
- 도구의 심각도와 상관없이 항목마다 기준을 정해 둔다. 측정값이 없는 기준은 통과로 세지 않고 '미측정'으로 남긴다.
- 실패가 하나라도 있으면 종료 코드 1. 상태 안내는 DOM 변경 기록으로 판정한다 (실제 낭독 횟수가 아님).
사용법: python a11y_regress.py <fix-check JSON> [<fix-check JSON> ...] [--md <결과 md>]
  JSON을 여러 개 주면 앞에서부터 합친다 (뒤 파일에 있는 측정 묶음이 앞 파일의 같은 묶음을 바꾼다 — 예: 입력 측정만 다시 돌린 결과).
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

PASS, FAIL, NONE = "통과", "실패", "미측정"
INPUT_RULES = [".mapping-pick select", ".validation-form input", ".validation-form select", ".validation-form textarea", ".glossary-search input",
               ".glossary-search select", ".review-controls select", ".review-replace-row select", ".automation-period input", ".integration-automation input",
               ".login-card input", ".admin-form input", ".admin-actions input", ".wk-home input", ".wk-home select", ".wk-home textarea", ".bd-input", ".bd-choice select"]  # fmt: skip


@dataclass
class Result:
    item: str
    rule: str
    status: str
    evidence: str = ""


def find(data: dict, group: str, prefix: str) -> dict | None:
    return next((x for x in data.get(group, []) if str(x.get("what", x.get("mode", x.get("state", x.get("dialog", ""))))).startswith(prefix)), None)


def upload_texts(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "status" and "파일 업로드" in x.get("where", "")]


def pick_changes(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "status" and "pickcount" in x.get("where", "")]


def alerts(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "alert"]


def check(out: list, item: str, rule: str, rec: dict | None, cond, evidence=None) -> None:
    if rec is None:
        out.append(Result(item, rule, NONE, "측정 기록 없음"))
        return
    try:
        ok = bool(cond(rec))
    except (KeyError, TypeError, IndexError, AttributeError) as e:
        out.append(Result(item, rule, NONE, f"기록 형식이 다름 ({type(e).__name__})"))
        return
    out.append(Result(item, rule, PASS if ok else FAIL, str(evidence(rec) if evidence else "")[:220]))


def starts_and_finish(r: dict) -> bool:
    t = upload_texts(r["live"])
    return sum(x.startswith("파일 ") and "시작" in x for x in t) == 1 and sum(x.startswith("업로드를 마쳤습니다") for x in t) == 1


def judge(data: dict) -> list[Result]:
    out: list[Result] = []
    # ---- D-01 업로드 이름·조작
    check(out, "D-01", "조작 요소 역할 button, 이름에 보이는 '파일 선택' 포함, 설명에 지원 형식", find(data, "D-01", "업로드 조작 요소"),
          lambda r: r["role"] == "button" and "파일 선택" in (r["name"] or "") and "xlsx" in (r["description"] or ""), lambda r: f"이름 {r['name']!r} · 설명 {r['description']!r}")  # fmt: skip
    for how in ("click", "space"):
        check(out, "D-01", f"{how}로 파일 선택 창이 열림", find(data, "D-01", f"{how}로"), lambda r: r["ok"])
    check(out, "D-01", "끌어놓기로 업로드됨 (결과 안내가 나옴)", find(data, "D-01", "끌어놓기"), lambda r: any(t.startswith("업로드를 마쳤습니다") for t in upload_texts(r["live"])))
    # ---- A1 판정 근거·업로드 중·완료 뒤
    check(out, "D-01·A1", "axe label-content-name-mismatch 위반 없음 (업로드 영역)", find(data, "A1", "axe label"),
          lambda r: "violations" in r and not r["violations"], lambda r: r.get("violations") or r.get("axe") or "위반 없음")  # fmt: skip
    if (r := find(data, "A1", "axe label")) and "axe" in r:  # axe를 돌리지 않았으면 통과로 세지 않는다
        out[-1] = Result("D-01·A1", out[-1].rule, NONE, r["axe"])
    check(out, "D-01", "업로드 중: 이름 유지, aria-disabled=true, 초점 유지", find(data, "A1", "업로드 중 ("),
          lambda r: r["visible_text"] == "파일 선택" and r["aria_disabled"] == "true" and r["focused"], lambda r: f"이름 {r['ax'].get('name')!r} · aria-disabled {r['aria_disabled']} · 설명 {r['describedby']}")  # fmt: skip
    check(out, "D-01", "업로드 중 다시 눌러도 파일 선택 창이 열리지 않음", find(data, "A1", "업로드 중 다시"), lambda r: not any(r["opened"].values()), lambda r: r["opened"])
    check(out, "D-01", "업로드 완료 뒤: 사용 가능, 설명 복귀, 초점 유지", find(data, "A1", "업로드 완료 뒤"),
          lambda r: r["aria_disabled"] is None and any("xlsx" in d for d in r["describedby"]) and r["focused"], lambda r: f"aria-disabled {r['aria_disabled']} · 초점 {r['focused']}")  # fmt: skip
    # ---- D-02 상태 안내 (DOM 변경 기록)
    check(out, "D-02", "상태 영역이 메시지보다 먼저 있음", find(data, "D-02", "업로드 영역의 상태"), lambda r: len(r["status_regions_in_upload"]) >= 1)
    check(out, "D-02", "① 성공: 시작·결과 각 1회, 초점 이동 없음", find(data, "D-02", "①"), lambda r: starts_and_finish(r) and r["focus_kept"], lambda r: upload_texts(r["live"]))
    check(out, "D-02", "② 같은 결과 반복도 시작·결과 각 1회", find(data, "D-02", "②"), starts_and_finish, lambda r: upload_texts(r["live"]))
    check(out, "D-02", "③ 일부 실패: '이번 요청 2개 중 성공 1개, 실패 1개', alert 없음", find(data, "D-02", "③"),
          lambda r: any("2개 중 성공 1개, 실패 1개" in t for t in upload_texts(r["live"])) and not alerts(r["live"]), lambda r: upload_texts(r["live"]))  # fmt: skip
    check(out, "D-02", "④ 다시 그리기만 하면 업로드 안내 0회", find(data, "D-02", "④"), lambda r: not upload_texts(r["live"]), lambda r: upload_texts(r["live"]))
    check(out, "D-02", "⑤ 요청 전체 실패: 결과 안내 없음, alert 1회", find(data, "D-02", "⑤"),
          lambda r: not any(t.startswith("업로드를 마쳤습니다") for t in upload_texts(r["live"])) and len(alerts(r["live"])) == 1, lambda r: (upload_texts(r["live"]), alerts(r["live"])))  # fmt: skip
    check(out, "D-02", "⑥ 지원하지 않는 형식만: alert만, 업로드 안내 없음", find(data, "D-02", "⑥"),
          lambda r: len(alerts(r["live"])) == 1 and not upload_texts(r["live"]), lambda r: alerts(r["live"]))  # fmt: skip
    check(out, "D-02", "⑦ 완료 뒤 목록 갱신: 시작·결과 각 1회, 고른 개수 상태는 다시 바뀌지 않음", find(data, "D-02", "⑦"),
          lambda r: starts_and_finish(r) and not pick_changes(r["live"]), lambda r: (upload_texts(r["live"]), pick_changes(r["live"])))  # fmt: skip
    check(out, "D-02", "⑧ 정상 + 지원하지 않는 형식: 결과 안내 1회(성공 1개) + 형식 alert 1회", find(data, "D-02", "⑧"),
          lambda r: starts_and_finish(r) and len(alerts(r["live"])) == 1 and ".pdf" in alerts(r["live"])[0], lambda r: (upload_texts(r["live"]), alerts(r["live"])))  # fmt: skip
    check(out, "D-02", "⑨ 다른 화면에서 늦게 끝난 응답: 숨긴 영역 안에서만 바뀜, 돌아와도 다시 바뀌지 않음", find(data, "D-02", "⑨-2"),
          lambda r: not upload_texts(r["live"]), lambda r: r["status"])  # fmt: skip
    check(out, "D-02", "⑩ 업로드 중 로그아웃 → 다시 로그인: 지난 안내 없음, 오류 없음", find(data, "D-02", "⑩"),
          lambda r: r["status"]["text"] == "" and not upload_texts(r["live"]) and not r["page_errors"], lambda r: r)  # fmt: skip
    check(out, "D-02", "분석 자료 목록: 불러오는 중 → 불러옴", find(data, "D-02", "분석 자료 목록"),
          lambda r: any("불러오는 중" in t for t in r["while_loading_status"]) and any("불러왔습니다" in x["text"] for x in r["live"]))  # fmt: skip
    check(out, "D-02", "분석 결과: 같은 상태 요소 유지", find(data, "D-02", "분석 결과 불러오는 중"), lambda r: r["after"]["same_node"])
    check(out, "D-02", "분석 결과: 표가 남았을 때 '조건·요약'만 불러왔다고 안내, 표가 오면 전체 안내", find(data, "D-02", "분석 결과: 조건·요약만"),
          lambda r: "조건·요약" in r["while_tables_pending"] and "불러오는 중" in r["while_tables_pending"] and "표)" in r["after_tables"], lambda r: (r["while_tables_pending"], r["after_tables"]))  # fmt: skip
    check(out, "D-02", "분석 결과: 표 실패를 안내·화면에 남김 (다 불러온 것처럼 말하지 않음)", find(data, "D-02", "분석 결과: 결과 표만 실패"),
          lambda r: "불러오지 못했습니다" in r["status"] and r["visible_errors"], lambda r: (r["status"], r["visible_errors"]))  # fmt: skip
    # ---- D-03 입력 경계 (화면에 실제로 나타난 요소만)
    measured = [x for x in data.get("inputs", []) if "ratio_vs_outside" in x]
    for rule in INPUT_RULES:
        xs = [x for x in measured if rule in (x.get("rules") or [x.get("rule")])]
        if not xs:
            out.append(Result("D-03", f"{rule} 경계 3:1 (바깥·안쪽 배경)", NONE, "이번 측정에서 화면에 나타나지 않음"))
            continue
        low = min(xs, key=lambda x: min(x["ratio_vs_outside"], x["ratio_vs_inside"]))
        ok = all(min(x["ratio_vs_outside"], x["ratio_vs_inside"]) >= 3.0 for x in xs)
        out.append(Result("D-03", f"{rule} 경계 3:1 (바깥·안쪽 배경)", PASS if ok else FAIL,
                          f"{len(xs)}곳, 최저 {min(low['ratio_vs_outside'], low['ratio_vs_inside']):.3f} ({low['page']}, 바깥 {low['outside_bg']})"))  # fmt: skip
    for r in data.get("D-03", []):
        if "pass_3_to_1" in r:
            out.append(Result("D-03", f"{r['page']} {r['selector']} 경계 3:1", PASS if r["ratio_vs_outside"] >= 3 and r["ratio_vs_inside"] >= 3 else FAIL, f"{r['ratio_vs_outside']:.3f}"))
    # ---- D-04 스위치
    check(out, "D-04", "꺼짐: 트랙 vs 주변, 손잡이 vs 트랙 3:1", find(data, "D-04", "꺼짐"), lambda r: r["track_vs_around"] >= 3 and r["knob_vs_track"] >= 3,
          lambda r: f"{r['track_vs_around']:.3f} / {r['knob_vs_track']:.3f}")  # fmt: skip
    check(out, "D-04", "꺼짐 + 키보드 초점: 초점 링 3:1", find(data, "D-04", "꺼짐 + 키보드"), lambda r: r["ring_vs_around"] >= 3, lambda r: r.get("focus_ring"))
    check(out, "D-04", "켜짐 + 키보드 초점: 트랙·초점 링 3:1", find(data, "D-04", "켜짐 + 키보드"), lambda r: r["track_vs_around"] >= 3 and r["ring_vs_around"] >= 3)
    off, dis = find(data, "D-04", "꺼짐 (초점 없음)"), find(data, "D-04", "비활성")
    check(out, "D-04", "비활성은 조작 가능한 꺼짐과 색이 다름 (비활성은 3:1 예외)", dis, lambda r: off is not None and r["track"] != off["track"], lambda r: (r["track"], off and off["track"]))
    # ---- 초점 외곽선
    check(out, "초점", "취합 방식 라디오 카드 외곽선 3:1", find(data, "focus", "취합 방식"), lambda r: r["pass_3_to_1"], lambda r: f"{r['ratio_vs_around']:.3f} vs {r['around']}")
    check(out, "초점", "업로드 '파일 선택' 외곽선 3:1", find(data, "focus", "업로드 조작"), lambda r: r["pass_3_to_1"], lambda r: f"{r.get('ratio_vs_around', 0):.3f} vs {r.get('around')}")
    # ---- D-05 가입·최초 설정
    check(out, "D-05", "로그인은 설명 연결 없음 (그대로)", find(data, "D-05", "로그인"), lambda r: all(not f["describedby"] for f in r["fields"]))
    check(out, "D-05", "회원가입: 아이디·비밀번호·확인 칸에 상시 도움말 연결", find(data, "D-05", "회원가입"),
          lambda r: sum(bool(f["describedby"]) for f in r["fields"]) >= 3, lambda r: [f["describedby"] for f in r["fields"]])  # fmt: skip
    check(out, "D-05", "입력 뒤에도 도움말 보임", find(data, "D-05", "회원가입 — 입력 뒤"), lambda r: r["visible"] and all(r["visible"]))
    check(out, "D-05", "확인란 불일치·서버 거절 알림 유지", find(data, "D-05", "회원가입 — 서버 거절"), lambda r: r["alert"])
    check(out, "D-05", "최초 설정: 도움말 연결", find(data, "D-05", "최초 설정 ("), lambda r: sum(bool(f["describedby"]) for f in r["fields"]) >= 3)
    # ---- D-06 계정·점수
    check(out, "D-06", "계정 표시: div aria-label 없음, '로그인 계정'·'역할' 전달", find(data, "D-06", "계정 표시"),
          lambda r: r["aria_label_attr"] is None and "로그인 계정" in r["accessible_text"] and "역할" in r["accessible_text"], lambda r: r["accessible_text"])  # fmt: skip
    check(out, "D-06", "점수: aria-label 없음, '데이터 품질 점수' 전달", find(data, "D-06", "검토 점수 (완료"),
          lambda r: r["aria_label_attr"] is None and "데이터 품질 점수" in r["accessible_text"])  # fmt: skip
    check(out, "D-06", "지난 검사 점수 뜻 유지", find(data, "D-06", "검토 점수 (다시"), lambda r: "지난 검사 점수" in r["accessible_text"])
    check(out, "D-06", "조회 전용 안내 유지", find(data, "D-06", "다른 사용자 검토"), lambda r: r["readonly_note"] and "조회만" in r["readonly_note"])
    # ---- D-07 라벨 구조
    check(out, "D-07", "라디오: 라벨 누르면 그 입력만, 화살표로 이동, label 안에 div 없음", find(data, "D-07", "취합 방식"),
          lambda r: r["after_label_click"] == ["table:false", "sheets:true"] and r["after_arrow_up"] == ["table:true", "sheets:false"] and all("DIV" not in s["inner"] and s["wrap"] != "DIV" for s in r["structure"]))  # fmt: skip
    check(out, "D-07", "스위치: 라벨 연결·div 없음·역할 checkbox·이름·라벨 클릭과 Space로 바뀜", find(data, "D-07", "통계 요약"),
          lambda r: r["htmlFor"] == r["inputId"] and "DIV" not in r["children"] and r["ax"]["role"] == "checkbox" and r["ax"]["name"] == "통계 요약 시트 추가"
          and r["after_label_text_click"] != r["checked_initial"] and r["after_space"] == r["checked_initial"])  # fmt: skip
    # ---- 확인창 본문 연결과 초점
    probes = [x for x in data.get("dialog", []) if "first_focus" in x]
    if not probes:
        out.append(Result("확인창", "본문 연결·초점", NONE, "측정 기록 없음"))
    for d in probes:
        a = d["attrs"] or {}
        out.append(Result("확인창", f"{d['dialog']}: alertdialog의 aria-describedby가 본문을 가리킴 (바깥 요소 아님)",
                          PASS if a.get("describedby") and a.get("description") and a["description"][0] != "(없는 id)" and not a.get("ancestors_with_describedby") else FAIL,
                          f"{a.get('describedby')} → {a.get('description')}"))  # fmt: skip
        out.append(Result("확인창", f"{d['dialog']}: 제목 연결 유지", PASS if a.get("title") and d["ax"].get("name") else FAIL, str(a.get("title"))))
        moves = d["tab"] + d["shift_tab"]
        ok = d["first_focus"]["name"] == "취소" and all(m["in_dialog"] for m in moves) and d["escape"]["closed"] and d["escape"]["focus_on_trigger"] and d["cancel"]["closed"] and d["cancel"]["focus_on_trigger"]
        out.append(Result("확인창", f"{d['dialog']}: 처음 초점 '취소', Tab·Shift+Tab 안에서만, Esc·취소 뒤 원래 버튼", PASS if ok else FAIL,
                          f"처음 {d['first_focus']['name']} · 밖으로 {sum(not m['in_dialog'] for m in moves)}번 · Esc {d['escape']} · 취소 {d['cancel']}"))  # fmt: skip
        again = d.get("reopen_attrs") or {}
        out.append(Result("확인창", f"{d['dialog']}: 다시 열어도 같은 연결", PASS if again.get("describedby") == a.get("describedby") else FAIL, str(again.get("describedby"))))
    cmp_ = find(data, "dialog", "용어사전 삭제 두 건")
    check(out, "확인창", "본문이 다른 두 확인창에서 설명이 섞이지 않음", cmp_, lambda r: len(r["descriptions"]) == 2 and r["descriptions"][0] != r["descriptions"][1], lambda r: r["descriptions"])
    # ---- 범위
    out.append(Result("범위", "진단 주소 밖 요청 0", PASS if not data.get("blocked_out_of_scope") else FAIL, str(data.get("blocked_out_of_scope", []))[:200]))
    if data.get("error"):
        out.append(Result("측정", "측정 중 오류 없음", FAIL, str(data["error"])[:220]))
    return out


def merge(paths: list[Path]) -> dict:
    data: dict = {}
    for p in paths:
        data.update(json.loads(p.read_text(encoding="utf-8")))
    return data


def main() -> None:
    args = sys.argv[1:]
    md = Path(args[args.index("--md") + 1]) if "--md" in args else None
    srcs = [Path(a) for i, a in enumerate(args) if a != "--md" and (i == 0 or args[i - 1] != "--md")]
    res = judge(merge(srcs))
    n = {s: sum(r.status == s for r in res) for s in (PASS, FAIL, NONE)}
    lines = [f"# 접근성 회귀 판정 — {' + '.join(f'`{s.name}`' for s in srcs)}", "", f"통과 {n[PASS]} · 실패 {n[FAIL]} · 미측정 {n[NONE]} (미측정은 통과로 세지 않음)", "",
             "| 항목 | 기준 | 결과 | 근거 |", "|---|---|---|---|"]  # fmt: skip
    lines += [f"| {r.item} | {r.rule} | {r.status} | {r.evidence.replace('|', '/')} |" for r in res]
    text = "\n".join(lines) + "\n"
    if md:
        md.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    sys.exit(1 if n[FAIL] else 0)


if __name__ == "__main__":
    main()
