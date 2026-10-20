"""색 대비 계산 (WCAG 2.x 상대 휘도 공식, sRGB) — contrast_check.py·toggle_check.py·a11y_fix_check.py가 기록한 계산된 색을 쓴다.

- sRGB 선형화 경계값은 현재 sRGB 정의의 0.04045를 쓴다 (2026-10-20 진단 3차 보완 전에는 WCAG 2.0 문서의 0.03928).
  8비트 정수 색(0~255)에서는 두 경계 사이에 들어가는 값이 없어 결과가 같고, 알파 합성으로 생긴 소수 색에서만 달라질 수 있다.
- 반투명 색은 실제 뒤 배경과 알파 합성한 뒤 계산한다.
- 판정(기준 이상인지)은 반올림·내림 전의 원시 값(ratio)으로 한다. 보여 줄 때만 shown()으로 소수 셋째 자리까지 내린다.
사용법: python contrast_calc.py [<contrast_check.py 기록 JSON>]
  인자 없음: 1차 색 조합을 옛 경계·새 경계로 다시 계산해 값·판정 변화를 함께 출력
  기록 JSON: 그 기록의 전경·배경 색으로 다시 계산 (기록값은 1차 방식 — 경계 0.03928, 소수 둘째 자리 반올림 — 과 비교, 파일은 읽기만)
(다른 스크립트는 lum·ratio·shown·oklab_to_srgb·over를 import해 같은 계산을 쓴다)"""

import math

SRGB_THRESHOLD = 0.04045
OLD_THRESHOLD = 0.03928  # 1차 계산에 쓴 값 — 다시 계산해 비교할 때만


def lin(c: float, threshold: float = SRGB_THRESHOLD) -> float:
    c /= 255
    return c / 12.92 if c <= threshold else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb, threshold: float = SRGB_THRESHOLD) -> float:
    r, g, b = rgb
    return 0.2126 * lin(r, threshold) + 0.7152 * lin(g, threshold) + 0.0722 * lin(b, threshold)


def ratio(a, b, threshold: float = SRGB_THRESHOLD) -> float:
    """원시 대비 값 (반올림·내림 없음) — 판정은 이 값으로 한다."""
    x, y = sorted((lum(a, threshold), lum(b, threshold)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def shown(r: float) -> float:
    """보여 주기용: 소수 셋째 자리까지 내림 (올림으로 기준을 넘어 보이게 하지 않음). 판정에는 쓰지 않는다."""
    return math.floor(r * 1000) / 1000


def oklab_to_srgb(L: float, a: float, b: float):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    rl = 4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc
    gl = -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc
    bl = -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc
    enc = lambda x: 255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)  # noqa: E731
    return tuple(max(0, min(255, enc(v))) for v in (rl, gl, bl))


def over(fg, alpha: float, bg):
    return tuple(alpha * f + (1 - alpha) * k for f, k in zip(fg, bg, strict=True))


def main() -> None:
    WHITE = (255, 255, 255)
    ring = oklab_to_srgb(0.544342, -0.00891709, -0.24082)
    ring_on_white = over(ring, 0.2, WHITE)
    cases = [
        ("입력칸 테두리 #d1d5db vs 흰 배경 (내 계정 비밀번호 칸)", (209, 213, 219), WHITE, 3.0),
        ("업로드 영역 점선 테두리 #d1d5db vs 흰 배경 (기본 상태)", (209, 213, 219), WHITE, 3.0),
        ("스위치 꺼짐: 트랙 rgb(229,231,235) vs 주변 흰 배경", (229, 231, 235), WHITE, 3.0),
        ("스위치 꺼짐: 손잡이 흰색 vs 트랙 rgb(229,231,235)", WHITE, (229, 231, 235), 3.0),
        ("스위치 켜짐: 트랙 rgb(55,88,249) vs 주변 흰 배경", (55, 88, 249), WHITE, 3.0),
        ("스위치 켜짐: 손잡이 흰색 vs 트랙 rgb(55,88,249)", WHITE, (55, 88, 249), 3.0),
        (f"스위치 초점 링 oklab(0.544 -0.009 -0.241 / 0.2) → sRGB {tuple(round(x) for x in ring)} 20%를 흰 배경과 합성 = {tuple(round(x) for x in ring_on_white)} vs 흰 배경", ring_on_white, WHITE, 3.0),
        ("스위치 초점 링(합성 후) vs 꺼짐 트랙 rgb(229,231,235)", ring_on_white, (229, 231, 235), 3.0),
        ("버튼·링크 초점 외곽선 rgb(55,88,249) 2px vs 흰 배경", (55, 88, 249), WHITE, 3.0),
        ("메뉴 초점 외곽선 rgb(55,88,249) vs 활성 메뉴 배경 rgb(239,243,255)", (55, 88, 249), (239, 243, 255), 3.0),
        ("검토 화살표 글자 rgb(55,88,249) vs rgb(243,244,246)", (55, 88, 249), (243, 244, 246), 4.5),
        ("검토 위치 글자 rgb(91,100,114) vs rgb(243,244,246)", (91, 100, 114), (243, 244, 246), 4.5),
        ("모바일 분석 표 글자 rgb(31,41,55) vs 흰 배경", (31, 41, 55), WHITE, 4.5),
        ("비활성 메뉴 글자 rgb(156,163,175) vs rgb(249,250,251) (비활성 — 기준 예외)", (156, 163, 175), (249, 250, 251), 4.5),
    ]
    print("새 경계(0.04045) 원시값 | 보이는 값 | 판정 | 옛 경계(0.03928) 원시값 | 옛 판정 | 원시값 차이 | 대상")
    for name, a, b, need in cases:
        r, r_old = ratio(a, b), ratio(a, b, OLD_THRESHOLD)
        ok, ok_old = r >= need, r_old >= need
        print(f"{r:.6f} | {shown(r):.3f}:1 | 기준 {need}:1 {'충족' if ok else '미달'} | {r_old:.6f} | {'충족' if ok_old else '미달'} | {r - r_old:+.2e}{'' if ok == ok_old else ' 판정 바뀜'} | {name}")


def recalc_records(path: str) -> None:
    """contrast_check.py 기록(1차 방식)의 전경·배경 색으로 다시 계산해 판정 변화를 센다. 기록 파일은 읽기만 한다."""
    import json
    import re

    d = json.loads(open(path, encoding="utf-8").read())
    items = d if isinstance(d, list) else [x for v in d.values() for x in (v if isinstance(v, list) else [v])]
    rgb = lambda s: tuple(int(x) for x in re.findall(r"\d+", s)[:3])  # noqa: E731

    def need(it: dict) -> float:  # 글자: 큰 글자(24px 이상, 또는 18.66px 이상 굵게)는 3:1, 아니면 4.5:1. 비텍스트는 3:1
        if it.get("prop") != "text":
            return 3.0
        px, w = float(str(it.get("fontSize", "16")).rstrip("px") or 16), int(it.get("fontWeight", 400) or 400)
        return 3.0 if px >= 24 or (px >= 18.66 and w >= 700) else 4.5

    print("기록값(1차 방식) → 새 원시값(경계 0.04045) | 보이는 값 | 옛 경계 원시값 | 기준 판정 | 대상")
    n = changed = 0
    for it in items:
        if not isinstance(it, dict) or "fg" not in it or "bg" not in it or not isinstance(it.get("ratio"), (int, float)):
            continue
        fg, bg, req = rgb(it["fg"]), rgb(it["bg"]), need(it)
        r_new, r_old = ratio(fg, bg), ratio(fg, bg, OLD_THRESHOLD)
        n += 1
        changed += (it["ratio"] >= req) != (r_new >= req)
        print(f"{it['ratio']:>6} → {r_new:.6f} | {shown(r_new):.3f} | {r_old:.6f} | 기준 {req} {'충족' if r_new >= req else '미달'}"
              f"{'' if (it['ratio'] >= req) == (r_new >= req) else ' ← 판정 바뀜'} | {it.get('sel')} {it.get('prop')} {str(it.get('text', ''))[:16]}")  # fmt: skip
    print(f"합계 {n}개 · 판정 바뀜 {changed}개")


if __name__ == "__main__":
    import sys

    recalc_records(sys.argv[1]) if len(sys.argv) > 1 else main()
