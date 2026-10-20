"""웹 표준·자동 접근성 원시 결과 재집계 — 새로 검사하지 않는다. 사용법: python summarize_a11y.py <raw 폴더> <출력 md>

- 72개 화면 상태 표: 첫 실행(a11y/) 결과에 재실행(a11y-retry/)으로 도달한 4개 상태를 합친다 (재실행 여부 표시).
- 공통 원인별: 도구가 보고한 발생 건수(원시 메시지·노드 수), 영향받은 화면 상태 수, 근본 원인과 소스 위치.
- 어느 원인에도 연결되지 않은 메시지는 '미분류'로 남긴다 (빠뜨리지 않음).
"""

import collections
import json
import re
import sys
from pathlib import Path

# (원인 ID, 도구, 메시지/규칙 정규식, DOM 발췌·대상 정규식, 근본 원인, 소스 위치)
CAUSES = [
    ("H1", "nu", r"aria-label.*must not be specified on any .div", r"side-account", "계정 표시 div에 role 없이 aria-label", "frontend/src/App.tsx:291"),
    ("H2", "nu", r"aria-label.*must not be specified on any .div", r"review-score-value", "점수 표시 div에 role 없이 aria-label", "frontend/src/ReviewPanel.tsx:469"),
    ("H3", "nu", r"div.*not allowed as child of element .label", r"wk-mode-radio", "Tailgrids RadioInput이 label 안에 div를 둠 (취합 방식 카드)",
     "frontend/src/components/tailgrids/core/radio-input.tsx · 사용처 Validation.tsx:342"),
    ("H4", "nu", r"div.*not allowed as child of element .label", r'class="relative"><input', "Tailgrids Toggle이 label 안에 div를 둠 (통계 요약 스위치)",
     "frontend/src/components/tailgrids/core/toggle.tsx · 사용처 Bundle.tsx"),
    ("H5", "nu", r"heading .h3.*follows the heading .h1", r"bd-sub", "묶기 화면 h1 다음에 h3 (h2 없음)", "frontend/src/Bundle.tsx:388·419"),
    ("W1", "nu", r"aria-disabled.*unnecessary", r"side-link is-disabled", "disabled 버튼에 aria-disabled 중복", "frontend/src/App.tsx:300"),
    ("W2", "nu", r"input.*descendant of an element with the attribute .role=button", r"accept=\".xlsx", "업로드 영역(role=button) 안에 숨긴 파일 입력",
     "frontend/src/Upload.tsx:72-104"),
    ("W3", "nu", r"Consider using the .h1. element as a top-level heading", r".*", "403 화면에 h1이 둘 (본문 h1 + 숨긴 홈 h1)", "frontend/src/App.tsx:147 (숨긴 홈 유지)"),
    ("A1", "axe", r"^label-content-name-mismatch$", r"upload-drop", "업로드 영역 접근 가능한 이름에 보이는 '파일 선택' 없음", "frontend/src/Upload.tsx:75"),
    ("A2", "axe", r"^label-content-name-mismatch$", r"wk-rowpick|열 .* 선택", "열 선택 버튼 이름 'A열 시도 선택' vs 보이는 'A 시도'", "frontend/src/MappingWorkspace.tsx:460"),
    ("A3", "axe", r"^scrollable-region-focusable$", r"upload-table-wrap", "분석 결과 표 스크롤 영역에 tabindex·이름 없음", "frontend/src/Analysis.tsx:1168·1231·1265·1308"),
    ("A4", "axe-incomplete", r"^color-contrast$", r".*", "axe가 배경을 정하지 못해 판정 보류 (측정으로 확인)", "여러 곳"),
    ("A5", "axe-incomplete", r"^aria-prohibited-attr$", r".*", "H1·H2와 같은 원인 (div의 aria-label)", "App.tsx:291 · ReviewPanel.tsx:469"),
    ("H5b", "axe-bp", r"^heading-order$", r".*", "H5와 같은 원인", "frontend/src/Bundle.tsx:388·419"),
]


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def load_round1(raw: Path) -> list[dict]:
    """1차: 첫 실행(a11y/)에 재실행(a11y-retry/)으로 도달한 상태를 합친다."""
    first = load(raw / "a11y" / "a11y-results.json")
    retry = {(r["viewport"], r["id"]): r for r in load(raw / "a11y-retry" / "a11y-results.json")}
    states = []
    for r in first:
        k = (r["viewport"], r["id"])
        if r["status"] != "검사함" and k in retry:
            states.append({**retry[k], "_run": "재실행(선택자 수정)", "_first_error": r.get("error", "")[:80]})
        else:
            states.append({**r, "_run": "첫 실행"})
    return states


def load_dir(folder: Path) -> list[dict]:
    """한 번에 72개 상태를 검사한 폴더 (재실행 없음)."""
    return [{**r, "_run": "첫 실행"} for r in load(folder / "a11y-results.json")]


def aggregate(states: list[dict]):
    agg = {c[0]: {"raw": 0, "nodes": 0, "states": set()} for c in CAUSES}
    unmapped = collections.Counter()
    for s in states:
        key = f"{s['viewport'][0]}:{s['id']}"
        items = [("nu", m["msg"], m.get("extract", ""), 1) for m in s.get("nu", []) if m["type"] == "error" or m.get("sub") == "warning"]
        items += [("axe", v["id"], json.dumps(v["targets"], ensure_ascii=False), v["nodes"]) for v in s.get("axe_violations", [])]
        items += [("axe-incomplete", v["id"], json.dumps(v["targets"], ensure_ascii=False), v["nodes"]) for v in s.get("axe_incomplete", [])]
        items += [("axe-bp", v["id"], json.dumps(v["targets"], ensure_ascii=False), v["nodes"]) for v in s.get("axe_best_practice", [])]
        for tool, msg, ext, nodes in items:
            for cid, ctool, mre, xre, *_ in CAUSES:
                if ctool == tool and re.search(mre, msg) and re.search(xre, ext):
                    agg[cid]["raw"] += 1
                    agg[cid]["nodes"] += nodes
                    agg[cid]["states"].add(key)
                    break
            else:
                unmapped[(tool, msg[:90])] += 1
    return agg, unmapped


def main() -> None:
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    states = load_round1(raw)
    agg, unmapped = aggregate(states)
    lines = ["# 웹 표준·자동 접근성 재집계 (원시 결과 기준, 새 검사 없음)", "",
             f"- 화면 상태: {len(states)}개 (데스크톱·모바일 각 {len(states) // 2}) · 검사함 {sum(s['status'] == '검사함' for s in states)} · "
             f"재실행으로 도달 {sum(s['_run'] != '첫 실행' for s in states)}", "",
             "## 공통 원인별 집계", "", "| 원인 | 도구 | 원시 발생(메시지) | 노드 합(axe) | 영향 상태 수 | 근본 원인 | 소스 위치 |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    for cid, tool, _, _, cause, src in CAUSES:
        a = agg[cid]
        lines.append(f"| {cid} | {tool} | {a['raw']} | {a['nodes'] if tool != 'nu' else '-'} | {len(a['states'])} | {cause} | `{src}` |")
    lines += ["", f"- 미분류 메시지: {sum(unmapped.values())}건" + ("" if not unmapped else " — " + "; ".join(f"{t} {m} ×{n}" for (t, m), n in unmapped.items())), ""]
    lines += ["## 72개 화면 상태와 결과", "", "| # | 화면 | 상태 | 사용자 | 경로 | 실행 | Nu 오류 | Nu 경고 | axe 위반(규칙) | axe 판정 보류 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(states, 1):
        ne = sum(1 for m in s.get("nu", []) if m["type"] == "error")
        nw = sum(1 for m in s.get("nu", []) if m.get("sub") == "warning")
        av = ", ".join(f"{v['id']}({v['nodes']})" for v in s.get("axe_violations", [])) or "-"
        ai = ", ".join(v["id"] for v in s.get("axe_incomplete", [])) or "-"
        lines.append(f"| {i} | {s['viewport']} | {s['id']} — {s['desc']} | {s['user'] or '로그인 전'} | `{s['path']}` | {s['_run']} | {ne} | {nw} | {av} | {ai} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[:24]))


if __name__ == "__main__":
    main()
