"""웹 표준·자동 접근성 수정 전후 비교 (원시 결과만 읽는다). 사용법: python compare_a11y.py <1차 raw 폴더> <수정 후 a11y 폴더> <출력 md>

- 같은 원인 분류(summarize_a11y.CAUSES)로 전후의 원시 발생 수·영향 상태 수를 나란히 둔다.
- 수정 후에 새로 생긴(어느 원인에도 연결되지 않은) 메시지는 따로 남긴다 — 경고를 숨겨 통과로 만들지 않기 위해.
- 서버 문서(CSS 등)의 메시지도 종류별로 전후 비교한다 (수정하지 않은 CSS 도구 미지원 경고는 그대로 유지돼야 함).
"""

import collections
import json
import re
import sys
from pathlib import Path

from summarize_a11y import CAUSES, aggregate, load_dir, load_round1

RAW, AFTER, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])


def css_kinds(path: Path) -> dict:
    docs = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for name, msgs in docs.items():
        c = collections.Counter()
        for m in msgs or []:
            q = re.findall(r"“([^”]*)”", m["message"])
            c[f"{m['type']}: {q[0] if q else m['message'][:40]}"] += 1
        out[re.sub(r"index-[^.]+\.css", "index-*.css", name)] = dict(c)
    return out


def main() -> None:
    before, after = load_round1(RAW), load_dir(AFTER)
    b_agg, b_un = aggregate(before)
    a_agg, a_un = aggregate(after)
    lines = ["# 웹 표준·자동 접근성 수정 전후 비교", "",
             f"- 수정 전: 1차 원시 결과 {len(before)}개 상태 (frontend `4e943da6…`) · 수정 후: {len(after)}개 상태 — 검사함 {sum(s['status'] == '검사함' for s in after)}", "",
             "| 원인 | 도구 | 근본 원인 | 수정 전 원시 | 수정 전 상태 | 수정 후 원시 | 수정 후 상태 | 소스 위치 |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for cid, tool, _, _, cause, src in CAUSES:
        b, a = b_agg[cid], a_agg[cid]
        lines.append(f"| {cid} | {tool} | {cause} | {b['raw']} | {len(b['states'])} | {a['raw']} | {len(a['states'])} | `{src}` |")
    lines += ["", f"- 수정 전 미분류 메시지: {sum(b_un.values())}건", f"- 수정 후 미분류(새로 생긴) 메시지: {sum(a_un.values())}건"]
    for (tool, msg), n in sorted(a_un.items(), key=lambda x: -x[1]):
        lines.append(f"  - {tool} ×{n}: {msg}")
    tot = lambda st, key, cond: sum(1 for s in st for m in (s.get(key) or []) if cond(m))  # noqa: E731
    lines += ["", "| 합계 | 수정 전 | 수정 후 |", "|---|---|---|",
              f"| Nu 오류(렌더링 DOM) | {tot(before, 'nu', lambda m: m['type'] == 'error')} | {tot(after, 'nu', lambda m: m['type'] == 'error')} |",
              f"| Nu 경고 | {tot(before, 'nu', lambda m: m.get('sub') == 'warning')} | {tot(after, 'nu', lambda m: m.get('sub') == 'warning')} |",
              f"| axe 위반(규칙×상태) | {tot(before, 'axe_violations', lambda m: True)} | {tot(after, 'axe_violations', lambda m: True)} |",
              f"| axe 판정 보류(규칙×상태) | {tot(before, 'axe_incomplete', lambda m: True)} | {tot(after, 'axe_incomplete', lambda m: True)} |",
              f"| axe 모범 사례(규칙×상태) | {tot(before, 'axe_best_practice', lambda m: True)} | {tot(after, 'axe_best_practice', lambda m: True)} |", ""]  # fmt: skip
    b_css = css_kinds(RAW / "a11y-retry" / "nu-server-documents.json")
    a_css = css_kinds(AFTER / "nu-server-documents.json")
    lines += ["## 서버 문서 (받은 그대로)", "", "| 문서 | 수정 전 | 수정 후 |", "|---|---|---|"]
    for k in sorted(set(b_css) | set(a_css)):
        lines.append(f"| {k} | {b_css.get(k, '-')} | {a_css.get(k, '-')} |")
    lines += ["", "## 상태별 (Nu 오류 / axe 위반 규칙)", "", "| 화면 | 상태 | 수정 전 Nu 오류 | 수정 후 Nu 오류 | 수정 전 axe 위반 | 수정 후 axe 위반 |", "|---|---|---|---|---|---|"]
    bmap = {(s["viewport"], s["id"]): s for s in before}
    for s in after:
        b = bmap.get((s["viewport"], s["id"]), {})
        ne = lambda x: sum(1 for m in (x.get("nu") or []) if m["type"] == "error")  # noqa: E731
        av = lambda x: ", ".join(v["id"] for v in x.get("axe_violations") or []) or "-"  # noqa: E731
        lines.append(f"| {s['viewport']} | {s['id']} | {ne(b)} | {ne(s)} | {av(b)} | {av(s)} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
