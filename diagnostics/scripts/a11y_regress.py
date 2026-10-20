"""접근성 회귀 판정 — 측정 스크립트(a11y_fix_check.py·nav_focus_check.py·button_transition_check.py)가 저장한 JSON을 읽어 정해 둔 기준으로 판정한다.

- 화면에 접속하지 않는다 (측정과 판정을 나눈다). 도구 심각도와 상관없이 항목마다 기준을 정해 둔다.
- 검사 범위는 --suite로 명시한다. 입력에 들어 있는 항목만 보고 범위를 추정하지 않는다. 고른 묶음의 기준은 모두 필수다.
- 결과: 통과 / 실패(제품 결함) / 미측정 / 측정 오류 / 입력 오류 / 해당 없음(사유를 남기는 경우만). 미측정·오류는 통과로 세지 않는다.
- 상태 안내 기준은 DOM 변경 기록, 초점 기준은 document.activeElement 기록이다 (실제 낭독이 아님).
- transition 묶음: 버튼마다 '정지 상태'(전환이 끝난 마지막 프레임)와 '전환 중'(다시 활성이 된 뒤의 모든 프레임)을 따로 판정한다.
  활성 프레임이 하나라도 기준(일반 글자 4.5:1, 큰 글자 3:1, 반올림 전 값) 미만이면 실패, 비활성→활성 전환을 관측하지 못했으면 미측정.
- 여러 파일: 측정 묶음은 뒤 파일이 앞을 바꾸고(출처 표에 남김), 범위 밖 요청·측정 오류는 모두 이어 붙인다.
  출처(빌드 자산·이미지)가 다른 파일을 합치면 --reuse-reason으로 재사용 이유를 적어야 전체 완료가 된다.

사용법: python a11y_regress.py --suite fix[,nav][,transition] [--partial ID,ID...] [--reuse-reason 글] [--md 결과.md] <JSON> [<JSON> ...]
  --suite·--partial 목록 규칙: 쉼표로 나누고 각 항목의 앞뒤 공백을 지운다, 빈 항목은 버린다, 같은 항목은 처음 것만 남긴다.
  그렇게 정리한 목록이 비면(빈 문자열·쉼표만·공백만) 사용법 오류(5). 판정 단계에서도 고른 기준이 0개면 전체 완료가 될 수 없다.
종료 코드: 0 전체 완료(고른 묶음의 필수 기준이 모두 측정되어 통과, 측정·입력 오류 없음, 범위 밖 요청 0건 기록)
          1 실패(제품 결함) 있음   2 불완전(미측정·측정 오류·입력 오류)   3 부분 검사 통과(--partial, 전체 완료 아님)
          4 판정기 자체 오류(예외 — 파이썬 기본 1이 '실패'로 읽히지 않게 따로 둠)   5 사용법 오류
출력: 표준 출력은 늘 UTF-8로 쓴다 (Windows 파이프 기본 cp949에서 '—' 같은 글자로 판정기가 죽지 않게).
"""

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, NONE, MERR, IERR, NA = "통과", "실패", "미측정", "측정 오류", "입력 오류", "해당 없음"
INCOMPLETE = (NONE, MERR, IERR)
EXIT_OK, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_PARTIAL, EXIT_TOOL, EXIT_USAGE = 0, 1, 2, 3, 4, 5
SUITES = ("fix", "nav", "transition")
INPUT_RULES = [".mapping-pick select", ".validation-form input", ".validation-form select", ".validation-form textarea", ".glossary-search input",
               ".glossary-search select", ".review-controls select", ".review-replace-row select", ".automation-period input", ".integration-automation input",
               ".login-card input", ".admin-form input", ".admin-actions input", ".wk-home input", ".wk-home select", ".wk-home textarea", ".bd-input", ".bd-choice select"]  # fmt: skip
DIALOGS = ["자동화 삭제", "용어사전 삭제 1", "용어사전 삭제 2", "표준 사전 빼기"]
# button_transition_check.py의 실행 — (실행 ID 앞부분, 버튼, 구분, 조건들). 조건마다 하나씩 모두 필수다.
TRANSITION_RUNS = [
    ("login.ctl", "로그인", "시험용 응답 (0.5초 뒤 401)", [f"{vp}.{m}" for vp in ("desktop", "mobile") for m in ("mouse_stay", "mouse_away", "keyboard")]),
    ("login.real", "로그인", "실제 서버 응답 (없는 아이디)", [f"{vp}.{i}" for vp in ("desktop", "mobile") for i in (1, 2)]),
    ("review", "사전 검토 열기", "실제 화면", [f"{vp}.{m}" for vp in ("desktop", "mobile") for m in ("hover", "away")]),
    ("verify", "검증 실행", "실제 화면", [f"{vp}.{m}" for vp in ("desktop", "mobile") for m in ("hover", "away")]),
    ("bundle_make", "묶기 엑셀 만들기", "실제 서버 응답 (구성 확인만 0.6초 붙잡음)", [f"{vp}.{m}" for vp in ("desktop", "mobile") for m in ("hover", "away")]),
    ("bundle_up", "묶기 위로", "실제 화면", ["desktop.keyboard"]),
]


class FormatError(Exception):
    """측정 기록의 모양이 기준이 읽는 모양과 다름 (입력 오류)."""


@dataclass
class Result:
    id: str
    suite: str
    item: str
    rule: str
    status: str
    evidence: str = ""


@dataclass
class Crit:
    id: str
    suite: str
    item: str
    rule: str
    fn: Callable[[dict], tuple[str, str]]


@dataclass
class Merged:
    data: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    input_errors: list[str] = field(default_factory=list)
    blocked_missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 입력 읽기·합치기


def merge(paths: list[Path]) -> Merged:
    """측정 JSON을 읽어 합친다. 잘못된 JSON·자료형은 입력 오류로 남기고, 오류·범위 밖 요청 기록은 어느 파일에 있어도 잃지 않는다."""
    m = Merged()
    m.data["blocked_out_of_scope"] = []
    m.data["error"] = []
    for p in paths:
        src = {"file": p.name, "meta": None, "groups": [], "overrode": []}
        m.sources.append(src)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            m.input_errors.append(f"{p.name}: JSON을 읽지 못함 ({type(e).__name__}: {str(e)[:120]})")
            continue
        if not isinstance(raw, dict):
            m.input_errors.append(f"{p.name}: 맨 위가 객체(JSON object)가 아님 ({type(raw).__name__})")
            continue
        src["meta"] = raw.get("meta") if isinstance(raw.get("meta"), dict) else None
        for k, v in raw.items():
            if k in ("meta", "browser"):
                continue
            if k == "blocked_out_of_scope":
                if isinstance(v, list) and all(isinstance(x, str) for x in v):
                    m.data[k] += v
                else:
                    m.input_errors.append(f"{p.name}: blocked_out_of_scope가 문자열 목록이 아님 ({type(v).__name__}) — 범위 기록으로 쓰지 않음")
                    m.blocked_missing.append(p.name)
                continue
            if k == "error":
                if v is None or v == []:
                    continue
                if isinstance(v, list):
                    m.data[k] += [x if isinstance(x, dict) else {"check": "(형식 다름)", "error": str(x)[:300]} for x in v]
                elif isinstance(v, dict):
                    m.data[k].append(v)
                elif isinstance(v, str):  # 측정 스크립트는 목록으로 남긴다 — 문자열은 내용을 보존하고 형식 문제로도 남긴다
                    m.data[k].append({"check": "(문자열 오류)", "error": v[:300]})
                    m.input_errors.append(f"{p.name}: error가 문자열 — 내용을 측정 오류로 보존")
                else:
                    m.data[k].append({"check": "(형식 다름)", "error": repr(v)[:300]})
                    m.input_errors.append(f"{p.name}: error 자료형을 지원하지 않음 ({type(v).__name__})")
                continue
            if not (isinstance(v, list) and all(isinstance(x, dict) for x in v)):
                m.input_errors.append(f"{p.name}: '{k}'가 객체 목록이 아님 ({type(v).__name__}) — 쓰지 않음")
                continue
            if k in m.data:
                src["overrode"].append(k)
            m.data[k] = v
            src["groups"].append(k)
        if "blocked_out_of_scope" not in raw:
            m.blocked_missing.append(p.name)
    for i, src in enumerate(m.sources):  # 뒤 파일이 바꾼 묶음은 앞 파일의 사용 목록에서 뺀다
        src["used"] = [g for g in src["groups"] if not any(g in later["groups"] for later in m.sources[i + 1 :])]
    return m


def source_key(src: dict) -> str | None:
    meta = src.get("meta") or {}
    return meta.get("frontend_asset") or meta.get("frontend_image")


# ---------------------------------------------------------------- 기준 도우미


def label(x: dict) -> str:
    """측정 기록의 이름: 스크립트마다 what·mode·state·dialog·page 중 하나를 쓴다."""
    return str(next((x[k] for k in ("what", "mode", "state", "dialog", "page", "run") if k in x), ""))


def find(data: dict, group: str, prefix: str) -> dict | None:
    return next((x for x in data.get(group, []) if label(x).startswith(prefix)), None)


def upload_texts(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "status" and "파일 업로드" in x.get("where", "")]


def pick_changes(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "status" and "pickcount" in x.get("where", "")]


def alerts(live: list) -> list[str]:
    return [x["text"] for x in live if x.get("role") == "alert"]


def starts_and_finish(r: dict) -> bool:
    t = upload_texts(r["live"])
    return sum(x.startswith("파일 ") and "시작" in x for x in t) == 1 and sum(x.startswith("업로드를 마쳤습니다") for x in t) == 1


def is_heading(f: dict, level: str, text: str | None = None) -> bool:
    return f.get("tag") == level and not f.get("in_hidden") and (text is None or f.get("text") == text)


def on(group: str, prefix: str, cond: Callable[[dict], bool], evidence: Callable[[dict], object] | None = None) -> Callable[[dict], tuple[str, str]]:
    """기록 하나에 대한 기준. 기록이 없으면 미측정, 모양이 다르면 입력 오류."""

    def fn(data: dict) -> tuple[str, str]:
        r = find(data, group, prefix)
        if r is None:
            return NONE, f"측정 기록 없음 ({group}: {prefix})"
        try:
            ok = bool(cond(r))
            ev = str(evidence(r) if evidence else "")[:220]
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            return IERR, f"기록 모양이 다름 ({type(e).__name__}: {str(e)[:80]})"
        return (PASS if ok else FAIL), ev

    return fn


def crit_list() -> list[Crit]:
    C: list[Crit] = []

    def add(cid: str, suite: str, item: str, rule: str, fn) -> None:
        C.append(Crit(cid, suite, item, rule, fn))

    # ---- fix: D-01 업로드 이름·조작, A1
    add("D01.name", "fix", "D-01", "조작 요소 역할 button, 이름에 보이는 '파일 선택' 포함, 설명에 지원 형식", on("D-01", "업로드 조작 요소",
        lambda r: r["role"] == "button" and "파일 선택" in (r["name"] or "") and "xlsx" in (r["description"] or ""), lambda r: f"이름 {r['name']!r}"))  # fmt: skip
    for how in ("click", "space"):
        add(f"D01.open.{how}", "fix", "D-01", f"{how}로 파일 선택 창이 열림", on("D-01", f"{how}로", lambda r: r["ok"]))
    add("D01.drop", "fix", "D-01", "끌어놓기로 업로드됨 (결과 안내)", on("D-01", "끌어놓기", lambda r: any(t.startswith("업로드를 마쳤습니다") for t in upload_texts(r["live"]))))

    def axe_a1(data: dict) -> tuple[str, str]:
        r = find(data, "A1", "axe label")
        if r is None:
            return NONE, "측정 기록 없음 (A1: axe label)"
        if "axe" in r:
            return NONE, str(r["axe"])  # axe를 돌리지 않음 — 통과로 세지 않는다
        if "violations" not in r:
            return IERR, "violations 없음"
        return (PASS if not r["violations"] else FAIL), str(r["violations"] or "위반 없음")[:220]

    add("A1.axe", "fix", "D-01·A1", "axe label-content-name-mismatch 위반 없음 (업로드 영역)", axe_a1)
    add("D01.busy", "fix", "D-01", "업로드 중: 이름 유지, aria-disabled=true, 초점 유지", on("A1", "업로드 중 (",
        lambda r: r["visible_text"] == "파일 선택" and r["aria_disabled"] == "true" and r["focused"], lambda r: r["describedby"]))  # fmt: skip
    add("D01.busy_picker", "fix", "D-01", "업로드 중 다시 눌러도 파일 선택 창이 열리지 않음", on("A1", "업로드 중 다시", lambda r: not any(r["opened"].values()), lambda r: r["opened"]))
    add("D01.after", "fix", "D-01", "업로드 완료 뒤: 사용 가능, 설명 복귀, 초점 유지", on("A1", "업로드 완료 뒤",
        lambda r: r["aria_disabled"] is None and any("xlsx" in d for d in r["describedby"]) and r["focused"]))  # fmt: skip
    # ---- fix: D-02 상태 안내 (DOM 변경 기록)
    add("D02.region", "fix", "D-02", "상태 영역이 메시지보다 먼저 있음", on("D-02", "업로드 영역의 상태", lambda r: len(r["status_regions_in_upload"]) >= 1))
    add("D02.c1", "fix", "D-02", "① 성공: 시작·결과 각 1회, 초점 이동 없음", on("D-02", "①", lambda r: starts_and_finish(r) and r["focus_kept"], lambda r: upload_texts(r["live"])))
    add("D02.c2", "fix", "D-02", "② 같은 결과 반복도 시작·결과 각 1회", on("D-02", "②", starts_and_finish, lambda r: upload_texts(r["live"])))
    add("D02.c3", "fix", "D-02", "③ 일부 실패: '2개 중 성공 1개, 실패 1개', alert 없음", on("D-02", "③",
        lambda r: any("2개 중 성공 1개, 실패 1개" in t for t in upload_texts(r["live"])) and not alerts(r["live"])))  # fmt: skip
    add("D02.c4", "fix", "D-02", "④ 다시 그리기만 하면 업로드 안내 0회", on("D-02", "④", lambda r: not upload_texts(r["live"])))
    add("D02.c5", "fix", "D-02", "⑤ 요청 전체 실패: 결과 안내 없음, alert 1회", on("D-02", "⑤",
        lambda r: not any(t.startswith("업로드를 마쳤습니다") for t in upload_texts(r["live"])) and len(alerts(r["live"])) == 1))  # fmt: skip
    add("D02.c6", "fix", "D-02", "⑥ 지원하지 않는 형식만: alert만", on("D-02", "⑥", lambda r: len(alerts(r["live"])) == 1 and not upload_texts(r["live"])))
    add("D02.c7", "fix", "D-02", "⑦ 완료 뒤 목록 갱신: 시작·결과 각 1회, 고른 개수 상태는 다시 바뀌지 않음", on("D-02", "⑦",
        lambda r: starts_and_finish(r) and not pick_changes(r["live"]), lambda r: pick_changes(r["live"])))  # fmt: skip
    add("D02.c8", "fix", "D-02", "⑧ 정상 + 지원하지 않는 형식: 결과 1회 + 형식 alert 1회", on("D-02", "⑧",
        lambda r: starts_and_finish(r) and len(alerts(r["live"])) == 1 and ".pdf" in alerts(r["live"])[0]))  # fmt: skip
    add("D02.c9", "fix", "D-02", "⑨ 다른 화면에서 늦게 끝난 응답: 돌아와도 다시 바뀌지 않음", on("D-02", "⑨-2", lambda r: not upload_texts(r["live"])))
    add("D02.c10", "fix", "D-02", "⑩ 업로드 중 로그아웃 → 다시 로그인: 지난 안내 없음, 오류 없음", on("D-02", "⑩",
        lambda r: r["status"]["text"] == "" and not upload_texts(r["live"]) and not r["page_errors"]))  # fmt: skip
    add("D02.sources", "fix", "D-02", "분석 자료 목록: 불러오는 중 → 불러옴", on("D-02", "분석 자료 목록",
        lambda r: any("불러오는 중" in t for t in r["while_loading_status"]) and any("불러왔습니다" in x["text"] for x in r["live"])))  # fmt: skip
    add("D02.same_node", "fix", "D-02", "분석 결과: 같은 상태 요소 유지", on("D-02", "분석 결과 불러오는 중", lambda r: r["after"]["same_node"]))
    add("D02.tables_pending", "fix", "D-02", "분석 결과: 표가 남았을 때 조건·요약만 안내, 표가 오면 전체 안내", on("D-02", "분석 결과: 조건·요약만",
        lambda r: "조건·요약" in r["while_tables_pending"] and "불러오는 중" in r["while_tables_pending"] and "표)" in r["after_tables"]))  # fmt: skip
    add("D02.tables_fail", "fix", "D-02", "분석 결과: 표 실패를 안내·화면에 남김", on("D-02", "분석 결과: 결과 표만 실패",
        lambda r: "불러오지 못했습니다" in r["status"] and r["visible_errors"]))  # fmt: skip

    # ---- fix: D-03 입력 경계 (화면에 실제로 나타난 요소만, 규칙마다 필수)
    def input_rule(rule: str):
        def fn(data: dict) -> tuple[str, str]:
            xs = [x for x in data.get("inputs", []) if "ratio_vs_outside" in x and rule in (x.get("rules") or [x.get("rule")])]
            if not xs:
                return NONE, "이번 측정에서 화면에 나타나지 않음"
            low = min(min(x["ratio_vs_outside"], x["ratio_vs_inside"]) for x in xs)
            return (PASS if low >= 3.0 else FAIL), f"{len(xs)}곳, 최저 {low:.3f}"

        return fn

    for rule in INPUT_RULES:
        add(f"D03.rule:{rule}", "fix", "D-03", f"{rule} 경계 3:1 (바깥·안쪽 배경)", input_rule(rule))
    for page, sel in (("로그인", ".login-card input[name=username]"), ("홈", ".wk-home input[type=search]"), ("내 계정", ".admin-form input[type=password]")):
        add(f"D03.case:{page}", "fix", "D-03", f"{page} {sel} 경계 3:1", on("D-03", page, lambda r: r["ratio_vs_outside"] >= 3 and r["ratio_vs_inside"] >= 3, lambda r: f"{r['ratio_vs_outside']:.3f}"))
    # ---- fix: D-04 스위치, 초점 외곽선
    add("D04.off", "fix", "D-04", "꺼짐: 트랙 vs 주변, 손잡이 vs 트랙 3:1", on("D-04", "꺼짐", lambda r: r["track_vs_around"] >= 3 and r["knob_vs_track"] >= 3))
    add("D04.off_focus", "fix", "D-04", "꺼짐 + 키보드 초점: 초점 링 3:1", on("D-04", "꺼짐 + 키보드", lambda r: r["ring_vs_around"] >= 3))
    add("D04.on_focus", "fix", "D-04", "켜짐 + 키보드 초점: 트랙·초점 링 3:1", on("D-04", "켜짐 + 키보드", lambda r: r["track_vs_around"] >= 3 and r["ring_vs_around"] >= 3))

    def disabled_distinct(data: dict) -> tuple[str, str]:
        off, dis = find(data, "D-04", "꺼짐 (초점 없음)"), find(data, "D-04", "비활성")
        if off is None or dis is None:
            return NONE, "꺼짐(초점 없음)·비활성 기록 중 없음"
        return (PASS if dis["track"] != off["track"] else FAIL), f"{dis['track']} vs {off['track']}"

    add("D04.disabled", "fix", "D-04", "비활성은 조작 가능한 꺼짐과 색이 다름 (비활성은 3:1 예외)", disabled_distinct)
    add("FOCUS.radio", "fix", "초점", "취합 방식 라디오 카드 외곽선 3:1", on("focus", "취합 방식", lambda r: r["pass_3_to_1"], lambda r: f"{r['ratio_vs_around']:.3f}"))
    add("FOCUS.upload", "fix", "초점", "업로드 '파일 선택' 외곽선 3:1", on("focus", "업로드 조작", lambda r: r["pass_3_to_1"], lambda r: f"{r['ratio_vs_around']:.3f}"))
    # ---- fix: D-05~D-07
    add("D05.login", "fix", "D-05", "로그인은 설명 연결 없음 (그대로)", on("D-05", "로그인", lambda r: all(not f["describedby"] for f in r["fields"])))
    add("D05.signup", "fix", "D-05", "회원가입: 세 칸에 상시 도움말 연결", on("D-05", "회원가입", lambda r: sum(bool(f["describedby"]) for f in r["fields"]) >= 3))
    add("D05.visible", "fix", "D-05", "입력 뒤에도 도움말 보임", on("D-05", "회원가입 — 입력 뒤", lambda r: r["visible"] and all(r["visible"])))
    add("D05.reject", "fix", "D-05", "서버 거절 알림 유지", on("D-05", "회원가입 — 서버 거절", lambda r: r["alert"]))
    add("D05.setup", "fix", "D-05", "최초 설정: 도움말 연결", on("D-05", "최초 설정 (", lambda r: sum(bool(f["describedby"]) for f in r["fields"]) >= 3))
    add("D06.account", "fix", "D-06", "계정 표시: aria-label 없음, '로그인 계정'·'역할' 전달", on("D-06", "계정 표시",
        lambda r: r["aria_label_attr"] is None and "로그인 계정" in r["accessible_text"] and "역할" in r["accessible_text"]))  # fmt: skip
    add("D06.score", "fix", "D-06", "점수: aria-label 없음, '데이터 품질 점수' 전달", on("D-06", "검토 점수 (완료",
        lambda r: r["aria_label_attr"] is None and "데이터 품질 점수" in r["accessible_text"]))  # fmt: skip
    add("D06.stale", "fix", "D-06", "지난 검사 점수 뜻 유지", on("D-06", "검토 점수 (다시", lambda r: "지난 검사 점수" in r["accessible_text"]))
    add("D06.readonly", "fix", "D-06", "조회 전용 안내 유지", on("D-06", "다른 사용자 검토", lambda r: r["readonly_note"] and "조회만" in r["readonly_note"]))
    add("D07.radio", "fix", "D-07", "라디오: 라벨 클릭은 그 입력만, 화살표 이동, label 안에 div 없음", on("D-07", "취합 방식",
        lambda r: r["after_label_click"] == ["table:false", "sheets:true"] and r["after_arrow_up"] == ["table:true", "sheets:false"]
        and all("DIV" not in s["inner"] and s["wrap"] != "DIV" for s in r["structure"])))  # fmt: skip
    add("D07.toggle", "fix", "D-07", "스위치: 라벨 연결·div 없음·역할 checkbox·이름·라벨 클릭과 Space", on("D-07", "통계 요약",
        lambda r: r["htmlFor"] == r["inputId"] and "DIV" not in r["children"] and r["ax"]["role"] == "checkbox" and r["ax"]["name"] == "통계 요약 시트 추가"
        and r["after_label_text_click"] != r["checked_initial"] and r["after_space"] == r["checked_initial"]))  # fmt: skip

    # ---- fix: 확인창 (필수 확인창 넷)
    def probe(name: str):
        def get(data: dict) -> dict | None:
            return next((x for x in data.get("dialog", []) if x.get("dialog") == name and "first_focus" in x), None)

        def desc(data: dict) -> tuple[str, str]:
            d = get(data)
            if d is None:
                return NONE, f"'{name}' 확인창 측정 없음"
            a = d["attrs"] or {}
            ok = a.get("describedby") and a.get("description") and a["description"][0] != "(없는 id)" and not a.get("ancestors_with_describedby")
            return (PASS if ok else FAIL), f"{a.get('describedby')} → {a.get('description')}"[:220]

        def title(data: dict) -> tuple[str, str]:
            d = get(data)
            if d is None:
                return NONE, f"'{name}' 확인창 측정 없음"
            return (PASS if (d["attrs"] or {}).get("title") and d["ax"].get("name") else FAIL), str((d["attrs"] or {}).get("title"))

        def keys(data: dict) -> tuple[str, str]:
            d = get(data)
            if d is None:
                return NONE, f"'{name}' 확인창 측정 없음"
            moves = d["tab"] + d["shift_tab"]
            ok = d["first_focus"]["name"] == "취소" and all(m["in_dialog"] for m in moves) and d["escape"]["closed"] and d["escape"]["focus_on_trigger"]
            ok = ok and d["cancel"]["closed"] and d["cancel"]["focus_on_trigger"]
            return (PASS if ok else FAIL), f"처음 {d['first_focus']['name']} · 밖으로 {sum(not m['in_dialog'] for m in moves)}번 · Esc {d['escape']} · 취소 {d['cancel']}"

        def reopen(data: dict) -> tuple[str, str]:
            d = get(data)
            if d is None:
                return NONE, f"'{name}' 확인창 측정 없음"
            again = d.get("reopen_attrs") or {}
            return (PASS if again.get("describedby") == (d["attrs"] or {}).get("describedby") else FAIL), str(again.get("describedby"))

        return desc, title, keys, reopen

    for name in DIALOGS:
        desc, title, keys, reopen = probe(name)
        add(f"DLG.{name}.desc", "fix", "확인창", f"{name}: alertdialog의 aria-describedby가 본문을 가리킴 (바깥 요소 아님)", desc)
        add(f"DLG.{name}.title", "fix", "확인창", f"{name}: 제목 연결 유지", title)
        add(f"DLG.{name}.keys", "fix", "확인창", f"{name}: 처음 초점 '취소', Tab 범위, Esc·취소 뒤 원래 버튼", keys)
        add(f"DLG.{name}.reopen", "fix", "확인창", f"{name}: 다시 열어도 같은 연결", reopen)
    add("DLG.distinct_bodies", "fix", "확인창", "본문이 다른 두 확인창에서 설명이 섞이지 않음", on("dialog", "용어사전 삭제 두 건",
        lambda r: len(r["descriptions"]) == 2 and r["descriptions"][0] != r["descriptions"][1]))  # fmt: skip
    add("DLG.distinct_ids", "fix", "확인창", "같은 화면의 두 확인창(용어사전·표준 사전) id가 다름", on("dialog", "같은 화면의 두 확인창 id",
        lambda r: r["glossary_id"] and r["standard_id"] and r["glossary_id"] != r["standard_id"], lambda r: (r["glossary_id"], r["standard_id"])))  # fmt: skip

    # ---- nav: 바로가기·메뉴·목록↔상세
    for name in ("홈", "실행 기록"):
        add(f"NAV.skip.{name}", "nav", "이동", f"본문 바로가기({name}): 첫 Tab, 초점 때 보임, 지금 화면 제목, 주소 그대로", on("nav", f"본문 바로가기 — {name}",
            lambda r, name=name: r["first_tab"].get("cls") == "skip-link" and r["visible_when_focused"] and is_heading(r["after_enter"], "H1", name)
            and r["after_enter"]["in_main"] and r["url_unchanged"]))  # fmt: skip
    for label in ("실행 기록", "검토 목록", "홈"):
        add(f"NAV.menu.{label}", "nav", "이동", f"메뉴 → {label}: 새 화면 제목으로", on("nav", f"메뉴 → {label}", lambda r: is_heading(r["after"], "H1", r["expected_title"])))
    add("NAV.list_detail", "nav", "이동", "검토 목록 → 상세: 상세 제목(h2)", on("nav", "검토 목록 → 상세 (Enter)", lambda r: is_heading(r["after"], "H2"), lambda r: r["after"]))
    add("NAV.detail_list", "nav", "이동", "상세 → 목록('검토 목록으로'): 연 버튼", on("nav", "상세 → 목록", lambda r: r["opener_key"] and r["after"].get("key") == r["opener_key"]))
    add("NAV.back", "nav", "이동", "상세에서 브라우저 뒤로: 연 버튼", on("nav", "상세에서 브라우저 뒤로", lambda r: r["opener_key"] and r["after"].get("key") == r["opener_key"]))
    add("NAV.forward", "nav", "이동", "브라우저 앞으로 상세: 일반 열기와 같은 상세 제목(h2)", on("nav", "브라우저 앞으로", lambda r: is_heading(r["after"], "H2"), lambda r: r["after"]))
    add("NAV.runs_detail", "nav", "이동", "실행 기록 → 검토 상세: 상세 제목", on("nav", "실행 기록 → 검토 상세", lambda r: is_heading(r["after"], "H2")))
    add("NAV.runs_back", "nav", "이동", "검토 상세에서 뒤로(실행 기록): 연 버튼", on("nav", "검토 상세에서 브라우저 뒤로 (실행",
        lambda r: r["opener_key"] and r["after"].get("key") == r["opener_key"]))  # fmt: skip
    add("NAV.analysis_open", "nav", "이동", "분석 지난 결과 열기: 결과 제목(h2)", on("nav", "분석: 지난 결과 열기 →", lambda r: is_heading(r["after"], "H2")))
    add("NAV.analysis_new", "nav", "이동", "분석 결과 → '새 분석': 연 줄('내 분석 결과')", on("nav", "분석: 결과 →", lambda r: r["after"].get("key") == "analysis-past"))
    add("NAV.keep.search", "nav", "이동", "검색 입력 중 초점 유지", on("nav", "홈 검색 입력", lambda r: r["after"].get("tag") == "INPUT"))
    add("NAV.keep.check", "nav", "이동", "체크(Space)로 고르기: 초점 유지", on("nav", "검증 대상 체크", lambda r: r["after"].get("tag") == "INPUT"))
    add("NAV.keep.replace", "nav", "이동", "주소만 고치는 파일 빼기: 제목으로 옮기지 않고 다음 Tab은 다음 버튼", on("nav", "묶기: 파일 빼기",
        lambda r: r["after"].get("tag") != "H1" and r["next_tab"].get("in_main") and r["next_tab"].get("cls") != "skip-link"))  # fmt: skip
    # ---- nav: summary 초점 복귀 뒤 기본 키보드 동작
    add("NAV.summary.tabindex", "nav", "이동", "'새 분석' 복귀로 초점을 받은 summary의 tabindex를 바꾸지 않음", on("summary", "'새 분석'으로 돌아온 직후",
        lambda r: r["focused"] and r["tabindex_attr"] is None and r["tabIndex"] == 0, lambda r: (r["tabindex_attr"], r["tabIndex"])))  # fmt: skip
    add("NAV.summary.shift_tab", "nav", "이동", "벗어난 뒤 Shift+Tab으로 다시 닿음", on("summary", "Tab으로 벗어난 뒤", lambda r: r["reached"]))
    add("NAV.summary.tab", "nav", "이동", "화면 제목에서 Tab으로 다시 닿음", on("summary", "화면 제목에서 Tab", lambda r: r["reached"]))
    add("NAV.summary.toggle", "nav", "이동", "Enter·Space로 펼치고 접힘", on("summary", "Enter·Space",
        lambda r: r["after_enter"] != r["open_before"] and r["after_space"] == r["open_before"]))  # fmt: skip
    # ---- nav: 이력·실패·대기
    add("NAV.history.alternate", "nav", "이동", "번갈아 열기: 앞으로는 B 상세 제목, 다시 뒤로는 B를 연 버튼", on("history", "번갈아 열기",
        lambda r: r["after_back_b"].get("key") == r["b"] and is_heading(r["after_forward_b"], "H2") and r["after_back_again"].get("key") == r["b"],
        lambda r: (r["after_forward_b"].get("text"), r["after_back_again"].get("key"))))  # fmt: skip
    add("NAV.history.same_url", "nav", "이동", "같은 상세를 다른 곳에서 열면 뒤로는 각자 연 버튼", on("history", "같은 상세를",
        lambda r: r["back_to_runs"].get("key") == r["runs_key"] and r["back_to_list"].get("key") == r["list_key"]))  # fmt: skip
    add("NAV.history.direct", "nav", "이동", "주소 직접 접근: 처음에는 초점을 옮기지 않고 첫 Tab은 바로가기", on("history", "상세 주소 직접 접근",
        lambda r: r["after_load"].get("where") == "(본문)" and r["first_tab"].get("cls") == "skip-link"))  # fmt: skip
    add("NAV.failure.review", "nav", "이동", "검토 상세를 찾지 못함: 상세 제목 또는 오류 안내로, 오류가 보임", on("failure", "검토 상세를 찾지 못함",
        lambda r: (is_heading(r["after"], "H2") or r["after"].get("cls") == "upload-error") and r["alerts"]))  # fmt: skip
    add("NAV.failure.analysis", "nav", "이동", "분석 결과를 불러오지 못함: 1초 안에 오류 안내로 (시간 초과를 기다리지 않음)", on("failure", "분석 결과를 불러오지 못함",
        lambda r: r["after_1s"].get("cls") == "upload-error" and r["after_3_6s"].get("cls") == "upload-error" and r["alerts"], lambda r: r["after_1s"]))  # fmt: skip
    add("NAV.stale.nav", "nav", "이동", "대기 중 다른 화면으로 → 이전 대기가 3.6초 뒤에도 초점을 옮기지 않음", on("stale", "상세 대기 중 다른 화면",
        lambda r: is_heading(r["after_nav"], "H1") and r["after_3_6s"].get("where") == "(본문)" and r["path"] != "/analysis"))  # fmt: skip

    def stale_logout(data: dict) -> tuple[str, str]:
        r = find(data, "stale", "상세 대기 중 로그아웃")
        if r is None:
            return NONE, "측정 기록 없음"
        if not r.get("valid_before_timeout"):
            return NONE, f"재로그인이 시간 초과(3초) 뒤에 끝나 시험이 성립하지 않음 ({r.get('relogin_ms_after_wait_start')}ms)"
        return (PASS if r["after_3_6s"].get("where") == "(본문)" and not r["page_errors"] else FAIL), str(r["after_3_6s"])

    add("NAV.stale.logout", "nav", "이동", "대기 중 로그아웃·다시 로그인 → 이전 대기가 초점을 옮기지 않음, 오류 없음", stale_logout)
    add("NAV.stale.moved", "nav", "이동", "대기 중 사용자가 옮긴 초점을 상세 도착 뒤에도 유지", on("stale", "상세 대기 중 사용자가",
        lambda r: r["after"].get("text") == r["moved_to"].get("text") and r["after"].get("tag") == r["moved_to"].get("tag")))  # fmt: skip
    add("NAV.stale.dialog", "nav", "이동", "대기 중 확인창이 열려 있으면 상세가 와도 초점이 확인창 안에 남음", on("stale", "상세 대기 중 확인창",
        lambda r: r["opened"].get("in_dialog") and r["while_open"].get("in_dialog")))  # fmt: skip

    # ---- nav: 긴 내용·표
    def where_full(data: dict) -> tuple[str, str]:
        r = find(data, "longtext", "검토 위치")
        if r is None:
            return NONE, "측정 기록 없음"
        if not r.get("summary_truncated"):
            return NONE, "측정 폭에서 잘리지 않아 시험이 성립하지 않음"
        f = r.get("full")
        return (PASS if f and f["visible"] and not f["clipped"] and f["text"] == r["summary_text"] else FAIL), str(f)

    add("NAV.long.where", "nav", "긴 내용", "검토 위치: 접힌 줄에서 잘림 → 펼치면 전체 (툴팁만이 아님)", where_full)

    def names(data: dict) -> tuple[str, str]:
        r = find(data, "longtext", "'전체 보기' 이름")
        if r is None:
            return NONE, "측정 기록 없음"
        if r["count"] < 2:
            return NONE, f"버튼 {r['count']}개 — 구분 여부를 판정할 수 없음"
        return (PASS if r["distinct"] == r["count"] and all(x.startswith("전체 보기") for x in r["names"]) else FAIL), str(r["names"][:2])[:220]

    add("NAV.long.names", "nav", "긴 내용", "'전체 보기' 이름이 항목마다 다름 (보이는 글자로 시작)", names)
    add("NAV.long.dialog", "nav", "긴 내용", "근거 창: 처음 초점 안, Tab이 뒤 화면 요소로 가지 않음, Esc 뒤 연 버튼", on("longtext", "근거 전체 보기 창",
        lambda r: r["first"]["in_dialog"] and all(m["where"] == "(본문)" or m["in_dialog"] for m in r["tab"]) and r["closed"] and r["focus_back_on_opener"]))  # fmt: skip

    def tables(vp: str, what: str):
        def fn(data: dict) -> tuple[str, str]:
            t = next((x for x in data.get("tables", []) if x.get("viewport") == vp), None)
            if t is None:
                return NONE, f"{vp} 표 측정 없음"
            ts = t["tables"]
            if not ts:
                return NONE, "표 없음"
            if what == "region":
                ok = all(x["scrolls"] == (x["tabindex"] == "0" and x["role"] == "region" and bool(x["label"])) for x in ts)
                return (PASS if ok else FAIL), "; ".join(f"{x['label'] or '-'}:{'넘침' if x['scrolls'] else '안 넘침'}/{x['tabindex']}" for x in ts)
            if what == "headers":
                return (PASS if all(x["th_col"] > 0 for x in ts) else FAIL), str([x["th_col"] for x in ts])
            if not any(x["scrolls"] for x in ts):
                return NA, "넘치는 표가 없어 키보드 스크롤 대상 없음"
            k = t.get("keyboard_scroll")
            return (PASS if k and k["after"] != k["before"] else FAIL), str(k)[:200]

        return fn

    for vp in ("desktop", "mobile"):
        add(f"NAV.table.{vp}.region", "nav", "표", f"분석 표({vp}): 넘치는 영역만 이름·Tab 정지점", tables(vp, "region"))
        add(f"NAV.table.{vp}.headers", "nav", "표", f"분석 표({vp}): 열 머리글 유지", tables(vp, "headers"))
        add(f"NAV.table.{vp}.keyboard", "nav", "표", f"분석 표({vp}): 넘치는 영역은 화살표로 스크롤", tables(vp, "keyboard"))

    # ---- transition: 비활성 → 활성 전환 중 글자 대비 (정지 상태와 따로)
    def need(f: dict) -> float:
        px, w = float(str(f["font_size"]).rstrip("px")), int(float(f["font_weight"]))
        return 3.0 if px >= 24 or (px >= 18.66 and w >= 700) else 4.5

    def run_rec(data: dict, run: str) -> dict | None:
        return next((x for x in data.get("transition", []) if x.get("run") == run), None)

    def static(run: str):
        def fn(data: dict) -> tuple[str, str]:
            r = run_rec(data, run)
            if r is None:
                return NONE, f"측정 기록 없음 ({run})"
            f = r["frames"][-1] if r["frames"] else None
            if f is None or r["final_disabled"] or not r["static_stable"]:
                return NONE, f"마지막 프레임이 활성·안정 상태가 아님 (비활성 {r['final_disabled']}, 안정 {r['static_stable']})"
            n = need(f)
            return (PASS if r["final_ratio"] >= n else FAIL), f"{r['final_ratio']:.4f} (기준 {n}, {r['font']})"

        return fn

    def moving(run: str):
        def fn(data: dict) -> tuple[str, str]:
            r = run_rec(data, run)
            if r is None:
                return NONE, f"측정 기록 없음 ({run})"
            if not r["observed"] or r["min_enabled_frame"] is None:
                return NONE, f"비활성→활성 전환을 관측하지 못함 (비활성 그려짐 {r['saw_disabled']}, 활성 시각 {r['enabled_at_ms']}) — 통과로 세지 않음"
            low = r["min_enabled_frame"]
            n = need(low)
            ok = r["min_enabled_ratio"] >= n and r["enabled_below_need"] == 0
            return (PASS if ok else FAIL), (f"활성 최저 {r['min_enabled_ratio']:.4f} (기준 {n}, {low['font_size']} {low['font_weight']}, {low['t']}ms) · "
                                            f"미달 프레임 {r['enabled_below_need']} · 활성 {r['enabled_at_ms']}ms")  # fmt: skip

        return fn

    for head, button, kind, conds in TRANSITION_RUNS:
        for cond in conds:
            run = f"{head}.{cond}"
            add(f"TR.{run}.static", "transition", f"{button} ({kind})", f"{cond}: 정지 상태(전환 끝) 글자 대비", static(run))
            add(f"TR.{run}.moving", "transition", f"{button} ({kind})", f"{cond}: 활성이 된 뒤 전환 중 모든 프레임 글자 대비", moving(run))
    return C


CRITS = crit_list()


# ---------------------------------------------------------------- 판정


def items(value: str) -> list[str]:
    """'a, b,,a ' → ['a', 'b']: 쉼표로 나누고 앞뒤 공백을 지우고, 빈 항목은 버리고, 중복은 처음 것만 남긴다."""
    out: list[str] = []
    for x in (v.strip() for v in value.split(",")):
        if x and x not in out:
            out.append(x)
    return out


def judge(m: Merged, suites: list[str], partial: list[str] | None = None, reuse_reason: str | None = None) -> list[Result]:
    out: list[Result] = []
    chosen = [c for c in CRITS if c.suite in suites and (partial is None or c.id in partial)]
    if not chosen:  # 고른 기준이 없으면 공통 기준(범위 밖 요청 등)만으로 '전체 완료'가 되지 않게 막는다
        out.append(Result("COMMON.no_criteria", "공통", "범위", "판정할 기준이 1개 이상 선택됨", IERR, f"선택된 기준 0개 (묶음 {suites}, 부분 {partial})"))
    for c in chosen:
        try:
            status, ev = c.fn(m.data)
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            status, ev = IERR, f"기록 모양이 다름 ({type(e).__name__})"
        out.append(Result(c.id, c.suite, c.item, c.rule, status, ev))
    # 공통: 범위 밖 요청 기록 (합친 결과에 한 번), 측정 오류, 입력 오류, 출처
    if m.blocked_missing:
        out.append(Result("COMMON.scope", "공통", "범위", "진단 주소 밖 요청 0건 (모든 입력 파일에 기록이 있어야 함)", NONE, f"기록 없음: {', '.join(m.blocked_missing)}"))
    else:
        b = m.data["blocked_out_of_scope"]
        out.append(Result("COMMON.scope", "공통", "범위", "진단 주소 밖 요청 0건 (모든 입력 파일에 기록이 있어야 함)", PASS if not b else FAIL, str(b)[:200] or "0건"))
    for e in m.data["error"]:
        out.append(Result("COMMON.measure_error", "공통", "측정", "측정 중 오류", MERR, f"{e.get('check')}: {str(e.get('error'))[:200]}"))
    for e in m.input_errors:
        out.append(Result("COMMON.input", "공통", "입력", "입력 형식", IERR, e))
    keys = {source_key(s) for s in m.sources}
    if len(m.sources) > 1 and (None in keys or len(keys) > 1):
        if reuse_reason:
            out.append(Result("COMMON.sources", "공통", "출처", "출처가 다른 측정을 합침", PASS, f"재사용 이유: {reuse_reason}"))
        else:
            out.append(Result("COMMON.sources", "공통", "출처", "출처가 다른(또는 출처 기록이 없는) 측정을 합침 — 재사용 이유 필요", IERR, "--reuse-reason 없음"))
    return out


def verdict(res: list[Result], partial: bool) -> tuple[str, int]:
    if any(r.status == FAIL for r in res):
        return "실패(제품 결함) 있음", EXIT_FAIL
    if any(r.status in INCOMPLETE for r in res):
        return "불완전 — 미측정·측정 오류·입력 오류가 있어 전체 완료가 아님", EXIT_INCOMPLETE
    if partial:
        return "부분 검사 통과 — 고른 기준만 판정함 (전체 완료 아님)", EXIT_PARTIAL
    return "전체 완료 — 고른 묶음의 필수 기준이 모두 측정되어 통과", EXIT_OK


def report(res: list[Result], m: Merged, args, title: str, code: int) -> str:
    counts = {s: sum(r.status == s for r in res) for s in (PASS, FAIL, NONE, MERR, IERR, NA)}
    lines = [f"# 접근성 회귀 판정 — 묶음 {', '.join(args.suite)}{' (부분 검사)' if args.partial else ''}", "",
             f"**판정: {title}** (종료 코드 {code})", "",
             " · ".join(f"{k} {v}" for k, v in counts.items()) + " — 미측정·측정 오류·입력 오류는 통과로 세지 않음", "",
             "## 입력과 출처", "", "| 파일 | 출처(빌드 자산·이미지·라벨) | 사용한 측정 묶음 | 뒤 파일이 바꾼 묶음 |", "|---|---|---|---|"]  # fmt: skip
    for s in m.sources:
        meta = s.get("meta") or {}
        origin = " · ".join(str(meta.get(k)) for k in ("frontend_asset", "frontend_image", "label", "started") if meta.get(k)) or "출처 기록 없음 (파일 이름으로만)"
        replaced = [g for g in s["groups"] if g not in s["used"]]
        lines.append(f"| `{s['file']}` | {origin} | {', '.join(s['used']) or '-'} | {', '.join(replaced) or '-'} |")
    if args.reuse_reason:
        lines += ["", f"- 재사용 이유: {args.reuse_reason}"]
    lines += ["- 공통 기록(범위 밖 요청·측정 오류)은 모든 파일의 것을 합쳐 한 번만 판정함. 같은 측정 묶음이 여러 파일에 있으면 뒤 파일 것을 씀(위 표)", "",
              "| ID | 묶음 | 항목 | 기준 | 결과 | 근거 |", "|---|---|---|---|---|---|"]  # fmt: skip
    lines += [f"| {r.id} | {r.suite} | {r.item} | {r.rule} | {r.status} | {r.evidence.replace('|', '/')} |" for r in res]
    return "\n".join(lines) + "\n"


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # 사용법 오류는 5 (2는 '불완전'에 씀)
        self.print_usage(sys.stderr)
        print(f"사용법 오류: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = Parser(description="접근성 회귀 판정")
    ap.add_argument("--suite", required=True, type=items, help="fix, nav, transition (쉼표로 여럿 — 공백·빈 항목·중복은 정리)")
    ap.add_argument("--partial", type=items, default=None, help="이 기준 ID만 판정 (부분 검사)")
    ap.add_argument("--reuse-reason", default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    if not args.suite:
        ap.error("--suite에 실제 묶음이 없음 (빈 값·쉼표만·공백만은 받지 않음)")
    bad = [s for s in args.suite if s not in SUITES]
    if bad:
        ap.error(f"알 수 없는 묶음 {bad} (가능: {', '.join(SUITES)})")
    if args.partial is not None:
        known = {c.id for c in CRITS if c.suite in args.suite}
        unknown = [x for x in args.partial if x not in known]
        if not args.partial:
            ap.error("--partial에 실제 기준 ID가 없음 (빈 값·쉼표만·공백만은 받지 않음)")
        if unknown:
            ap.error(f"고른 묶음에 없는 기준 ID {unknown}")
    m = merge(args.files)
    res = judge(m, args.suite, args.partial, args.reuse_reason)
    title, code = verdict(res, args.partial is not None)
    text = report(res, m, args, title, code)
    if args.md:
        args.md.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — 판정기 자체 오류는 4로 (판정 결과 1·2와 섞이지 않게)
        traceback.print_exc()
        sys.exit(EXIT_TOOL)
