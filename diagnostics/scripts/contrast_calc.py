"""색 대비 계산 (WCAG 2.x 상대 휘도 공식, sRGB) — contrast_check.py·toggle_check.py가 기록한 계산된 색을 그대로 쓴다.
반투명 색은 실제 뒤 배경과 알파 합성한 뒤 계산한다. 결과는 소수 셋째 자리까지 내림(올림으로 통과시키지 않음). 사용법: python contrast_calc.py
(다른 스크립트는 lum·ratio·oklab_to_srgb·over를 import해 같은 계산을 쓴다)"""
import math


def lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def ratio(a, b) -> float:
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return math.floor((x + 0.05) / (y + 0.05) * 1000) / 1000


def oklab_to_srgb(L: float, a: float, b: float):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_ ** 3, m_ ** 3, s_ ** 3
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
        (f"스위치 초점 링 oklab(0.544 -0.009 -0.241 / 0.2) → sRGB {tuple(round(x) for x in ring)} 20%를 흰 배경과 합성 = {tuple(round(x) for x in ring_on_white)} vs 흰 배경",
         ring_on_white, WHITE, 3.0),
        ("스위치 초점 링(합성 후) vs 꺼짐 트랙 rgb(229,231,235)", ring_on_white, (229, 231, 235), 3.0),
        ("버튼·링크 초점 외곽선 rgb(55,88,249) 2px vs 흰 배경", (55, 88, 249), WHITE, 3.0),
        ("메뉴 초점 외곽선 rgb(55,88,249) vs 활성 메뉴 배경 rgb(239,243,255)", (55, 88, 249), (239, 243, 255), 3.0),
        ("검토 화살표 글자 rgb(55,88,249) vs rgb(243,244,246)", (55, 88, 249), (243, 244, 246), 4.5),
        ("검토 위치 글자 rgb(91,100,114) vs rgb(243,244,246)", (91, 100, 114), (243, 244, 246), 4.5),
        ("모바일 분석 표 글자 rgb(31,41,55) vs 흰 배경", (31, 41, 55), WHITE, 4.5),
        ("비활성 메뉴 글자 rgb(156,163,175) vs rgb(249,250,251) (비활성 — 기준 예외)", (156, 163, 175), (249, 250, 251), 4.5),
    ]
    for name, a, b, need in cases:
        r = ratio(a, b)
        print(f"{r:7.3f}:1  기준 {need}:1  {'충족' if r >= need else '미달'}  {name}")


if __name__ == "__main__":
    main()
