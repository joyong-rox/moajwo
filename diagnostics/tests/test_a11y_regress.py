"""a11y_regress 판정 시험 — 네트워크·브라우저·운영 접근 없이, 측정 JSON 모양의 합성 자료로 판정과 CLI 종료 코드를 본다."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import a11y_regress as ar

SCRIPT = Path(ar.__file__)
START, DONE = "파일 1개 업로드를 시작했습니다.", "업로드를 마쳤습니다: 이번 요청 1개 중 성공 1개, 실패 0개."


def live(*texts, role="status", where="파일 업로드 sr-only"):
    return [{"role": role, "where": where, "text": t} for t in texts]


def el(tag, text=None, key=None, cls="", hidden=False, dialog=False):
    return {"where": "element", "tag": tag, "text": text, "key": key, "cls": cls, "in_hidden": hidden, "in_main": True, "in_dialog": dialog}


BODY = {"where": "(본문)"}


def probe(name):
    return {"dialog": name, "attrs": {"describedby": "_r_2_", "description": ["본문"], "ancestors_with_describedby": [], "title": ["제목"]}, "ax": {"name": "제목"},
            "first_focus": {"name": "취소", "in_dialog": True}, "tab": [{"name": "확인", "in_dialog": True}], "shift_tab": [{"name": "취소", "in_dialog": True}],
            "escape": {"closed": True, "focus_on_trigger": True}, "cancel": {"closed": True, "focus_on_trigger": True}, "reopen_attrs": {"describedby": "_r_2_"}}  # fmt: skip


def full_fix():
    """fix 묶음의 필수 기준을 모두 통과하는 최소 측정 기록 (합성)."""
    return {
        "meta": {"frontend_asset": "index-TEST.js", "label": "fix"},
        "D-01": [{"what": "업로드 조작 요소의 계산된 이름·설명", "role": "button", "name": "파일 선택", "description": "… xlsx, xls, csv …"},
                 {"what": "click로 파일 선택 열림", "ok": True}, {"what": "space로 파일 선택 열림", "ok": True}, {"what": "끌어놓기 업로드 동작", "live": live(START, DONE)}],
        "A1": [{"what": "axe label-content-name-mismatch — 홈 업로드 영역", "violations": []},
               {"what": "업로드 중 (시험에서 요청을 붙잡음)", "visible_text": "파일 선택", "aria_disabled": "true", "focused": True, "describedby": ["업로드·분석 중…"]},
               {"what": "업로드 중 다시 눌렀을 때 파일 선택 창", "opened": {"click_zone": False, "enter": False}},
               {"what": "업로드 완료 뒤", "aria_disabled": None, "describedby": ["xlsx, xls, csv"], "focused": True}],
        "D-02": [{"what": "업로드 영역의 상태 영역이 메시지 전에 있음", "status_regions_in_upload": ["sr-only"]},
                 {"what": "① 성공 1개", "live": live(START, DONE), "focus_kept": True}, {"what": "② 같은 성공 반복", "live": live(START, DONE)},
                 {"what": "③ 일부 실패", "live": live(START, "업로드를 마쳤습니다: 이번 요청 2개 중 성공 1개, 실패 1개.")}, {"what": "④ 다시 그리기만", "live": []},
                 {"what": "⑤ 요청 전체 실패", "live": live(START, "") + live("업로드 실패", role="alert", where="파일 업로드 wk-banner")},
                 {"what": "⑥ 지원하지 않는 형식만", "live": live("형식 제외", role="alert", where="파일 업로드 wk-banner")},
                 {"what": "⑦ 업로드 중 → 완료", "live": live(START, DONE)},
                 {"what": "⑧ 정상 + 지원하지 않는 형식", "live": live(START) + live("제외: a.pdf", role="alert") + live(DONE)},
                 {"what": "⑨-2 홈으로 돌아옴", "live": []}, {"what": "⑩ 업로드 중 로그아웃", "status": {"text": ""}, "live": [], "page_errors": []},
                 {"what": "분석 자료 목록 불러오는 중 → 완료", "while_loading_status": ["분석할 파일 목록을 불러오는 중…"], "live": [{"text": "파일 4개를 불러왔습니다."}]},
                 {"what": "분석 결과 불러오는 중 → 완료 (같은 상태 요소 유지)", "after": {"same_node": True}},
                 {"what": "분석 결과: 조건·요약만 왔고", "while_tables_pending": "조건·요약을 불러왔습니다. 표를 불러오는 중…", "after_tables": "불러왔습니다 (조건·요약, 결과·제외·근거 표)."},
                 {"what": "분석 결과: 결과 표만 실패", "status": "결과 표를 불러오지 못했습니다.", "visible_errors": ["결과 표를 불러오지 못했습니다."]}],
        "inputs": [{"page": "합성", "rule": r, "rules": [r], "ratio_vs_outside": 4.5, "ratio_vs_inside": 4.5} for r in ar.INPUT_RULES],
        "D-03": [{"page": p, "ratio_vs_outside": 4.54, "ratio_vs_inside": 4.54} for p in ("로그인", "홈", "내 계정")],
        "D-04": [{"state": "꺼짐", "track_vs_around": 4.5, "knob_vs_track": 4.5}, {"state": "꺼짐 + 키보드 초점", "ring_vs_around": 5.3},
                 {"state": "켜짐 + 키보드 초점", "track_vs_around": 5.3, "ring_vs_around": 5.3}, {"state": "꺼짐 (초점 없음)", "track": "rgb(118, 118, 118)"},
                 {"state": "비활성 (시험용 DOM 변경)", "track": "rgb(243, 244, 246)"}],
        "focus": [{"what": "취합 방식 라디오 카드 (키보드 초점)", "pass_3_to_1": True, "ratio_vs_around": 5.1}, {"what": "업로드 조작 요소 (키보드 초점)", "pass_3_to_1": True, "ratio_vs_around": 4.8}],
        "D-05": [{"mode": "로그인 (그대로여야 함)", "fields": [{"describedby": []}]}, {"mode": "회원가입", "fields": [{"describedby": ["a"]}] * 3},
                 {"mode": "회원가입 — 입력 뒤에도 도움말 보임", "visible": [True]}, {"mode": "회원가입 — 서버 거절(아이디 규칙)", "alert": ["거절"]},
                 {"mode": "최초 설정 (상태 응답 주입)", "fields": [{"describedby": ["a"]}] * 3}],
        "D-06": [{"what": "계정 표시", "aria_label_attr": None, "accessible_text": "로그인 계정 합성, 역할 일반"}, {"what": "검토 점수 (완료된 검토)", "aria_label_attr": None, "accessible_text": "데이터 품질 점수 100"},
                 {"what": "검토 점수 (다시 검사 전)", "accessible_text": "지난 검사 점수"}, {"what": "다른 사용자 검토", "readonly_note": "조회만 할 수 있습니다"}],
        "D-07": [{"what": "취합 방식 라디오", "after_label_click": ["table:false", "sheets:true"], "after_arrow_up": ["table:true", "sheets:false"], "structure": [{"inner": "SPAN", "wrap": "SPAN"}]},
                 {"what": "통계 요약 스위치", "htmlFor": "a", "inputId": "a", "children": "SPAN.block", "ax": {"role": "checkbox", "name": "통계 요약 시트 추가"},
                  "checked_initial": False, "after_label_text_click": True, "after_space": False}],
        "dialog": [probe(n) for n in ar.DIALOGS] + [{"dialog": "용어사전 삭제 두 건 비교", "descriptions": [["a"], ["b"]]},
                                                  {"dialog": "같은 화면의 두 확인창 id", "glossary_id": "_r_1_", "standard_id": "_r_2_"}],
        "blocked_out_of_scope": [],
    }  # fmt: skip


def full_nav():
    """nav 묶음의 필수 기준을 모두 통과하는 최소 측정 기록 (합성)."""
    menu = [{"what": f"메뉴 → {t}", "expected_title": t, "after": el("H1", t)} for t in ("실행 기록", "검토 목록", "홈")]
    return {
        "meta": {"frontend_asset": "index-TEST.js", "label": "nav"},
        "nav": [{"what": f"본문 바로가기 — {n}", "first_tab": el("A", "본문으로 바로가기", cls="skip-link"), "visible_when_focused": True, "after_enter": el("H1", n), "url_unchanged": True}
                for n in ("홈", "실행 기록")] + menu + [
                {"what": "검토 목록 → 상세 (Enter)", "after": el("H2", "검토 #1")}, {"what": "상세 → 목록 ('검토 목록으로')", "opener_key": "k1", "after": el("BUTTON", key="k1")},
                {"what": "상세에서 브라우저 뒤로", "opener_key": "k1", "after": el("BUTTON", key="k1")}, {"what": "브라우저 앞으로 (다시 상세)", "after": el("H2", "검토 #1")},
                {"what": "실행 기록 → 검토 상세", "after": el("H2")}, {"what": "검토 상세에서 브라우저 뒤로 (실행 기록)", "opener_key": "r1", "after": el("BUTTON", key="r1")},
                {"what": "분석: 지난 결과 열기 → 결과", "after": el("H2")}, {"what": "분석: 결과 → '새 분석'", "after": el("SUMMARY", key="analysis-past")},
                {"what": "홈 검색 입력", "after": el("INPUT")}, {"what": "검증 대상 체크 (Space)", "after": el("INPUT")},
                {"what": "묶기: 파일 빼기", "after": BODY, "next_tab": el("BUTTON", "이 파일 빼기")}],
        "summary": [{"what": "'새 분석'으로 돌아온 직후", "focused": True, "tabindex_attr": None, "tabIndex": 0}, {"what": "Tab으로 벗어난 뒤 Shift+Tab으로 다시", "reached": True},
                    {"what": "화면 제목에서 Tab으로 다시", "reached": True}, {"what": "Enter·Space로 펼치고 접기", "open_before": False, "after_enter": True, "after_space": False}],
        "history": [{"what": "번갈아 열기", "b": "k2", "after_back_b": el("BUTTON", key="k2"), "after_forward_b": el("H2"), "after_back_again": el("BUTTON", key="k2")},
                    {"what": "같은 상세를 실행 기록·검토 목록에서", "runs_key": "r1", "back_to_runs": el("BUTTON", key="r1"), "list_key": "k1", "back_to_list": el("BUTTON", key="k1")},
                    {"what": "상세 주소 직접 접근(새로고침)", "after_load": BODY, "first_tab": el("A", cls="skip-link")}],
        "failure": [{"what": "검토 상세를 찾지 못함 (시험용 404)", "after": el("H2"), "alerts": ["찾을 수 없음"]},
                    {"what": "분석 결과를 불러오지 못함 (시험용 500)", "after_1s": el("P", cls="upload-error"), "after_3_6s": el("P", cls="upload-error"), "alerts": ["오류"]}],
        "stale": [{"what": "상세 대기 중 다른 화면으로", "after_nav": el("H1", "실행 기록"), "after_3_6s": BODY, "path": "/runs"},
                  {"what": "상세 대기 중 로그아웃", "valid_before_timeout": True, "relogin_ms_after_wait_start": 700, "after_3_6s": BODY, "page_errors": []},
                  {"what": "상세 대기 중 사용자가 다른 요소로 이동", "moved_to": el("A", "내 계정"), "after": el("A", "내 계정")},
                  {"what": "상세 대기 중 확인창", "opened": el("BUTTON", dialog=True), "while_open": el("BUTTON", dialog=True)}],
        "longtext": [{"what": "검토 위치: 잘림", "summary_truncated": True, "summary_text": "합성.xlsx / 시도", "full": {"text": "합성.xlsx / 시도", "clipped": False, "visible": True}},
                     {"what": "'전체 보기' 이름", "count": 2, "distinct": 2, "names": ["전체 보기: a", "전체 보기: b"]},
                     {"what": "근거 전체 보기 창", "first": el("BUTTON", dialog=True), "tab": [BODY], "closed": True, "focus_back_on_opener": True}],
        "tables": [{"viewport": "desktop", "tables": [{"label": None, "role": None, "tabindex": None, "scrolls": False, "th_col": 8}]},
                   {"viewport": "mobile", "tables": [{"label": "결과 표", "role": "region", "tabindex": "0", "scrolls": True, "th_col": 8}], "keyboard_scroll": {"before": [0, 0], "after": [40, 0]}}],
        "blocked_out_of_scope": [],
    }  # fmt: skip


# button_transition_check.py의 실행 ID (판정기 목록과 따로 적어 둔다 — 판정기가 이 실행을 모두 요구하는지 함께 본다)
TR_RUNS = ([f"login.ctl.{vp}.{m}" for vp in ("desktop", "mobile") for m in ("mouse_stay", "mouse_away", "keyboard")]
           + [f"login.real.{vp}.{i}" for vp in ("desktop", "mobile") for i in (1, 2)]
           + [f"{b}.{vp}.{m}" for b in ("review", "verify", "bundle_make") for vp in ("desktop", "mobile") for m in ("hover", "away")]
           + ["bundle_up.desktop.keyboard"])  # fmt: skip


def frame(t, disabled, ratio, size="16px", weight="600"):
    return {"t": t, "disabled": disabled, "hover": False, "focused": False, "font_size": size, "font_weight": weight, "color": "rgb(255, 255, 255)",
            "bg": "rgb(55, 88, 249)", "opacity": "1", "around": "rgb(255, 255, 255)", "ratio": ratio}  # fmt: skip


def tr(run, low=5.349, size="16px", weight="600", observed=True, final_disabled=False):
    """button_transition_check.summarize()가 남기는 모양의 합성 기록: 비활성 프레임 뒤 활성 프레임, 활성 최저 low."""
    frames = [frame(0, True, 1.78, size, weight), frame(17, False, low, size, weight)] + [frame(34 + 17 * i, final_disabled, 5.349, size, weight) for i in range(3)]
    need = 3.0 if float(size.rstrip("px")) >= 24 or (float(size.rstrip("px")) >= 18.66 and int(weight) >= 700) else 4.5
    enabled = [f for f in frames if observed and not f["disabled"]]
    lowf = min(enabled, key=lambda f: f["ratio"]) if enabled else None
    return {"run": run, "button": "합성", "kind": "합성", "observed": observed, "saw_disabled": observed, "enabled_index": 1 if observed else None,
            "enabled_at_ms": 17 if observed else None, "frames_total": len(frames), "need": need, "font": f"{size} {weight}",
            "min_enabled_ratio": lowf["ratio"] if lowf else None, "min_enabled_frame": lowf, "enabled_below_need": sum(f["ratio"] < need for f in enabled),
            "final_ratio": frames[-1]["ratio"], "final_disabled": final_disabled, "static_stable": True, "frames_complete": True, "frames": frames}  # fmt: skip


def full_transition():
    """transition 묶음의 필수 기준(실행마다 정지·전환 중)을 모두 통과하는 측정 기록 (합성)."""
    return {"meta": {"frontend_asset": "index-TEST.js", "label": "transition"}, "transition": [tr(r) for r in TR_RUNS], "blocked_out_of_scope": []}


SCOPE_ONLY = {"blocked_out_of_scope": []}  # 측정 묶음 없이 범위 기록만 있는 입력 (5차 재현)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def run_cli(*args):
    # 출력 인코딩을 정하는 환경 변수를 빼고 실행한다 — 시험을 띄운 방식(PYTHONIOENCODING 유무)과 상관없이 같은 조건으로 본다
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)


def judged(tmp_path, suites, *datas, partial=None, reason=None):
    paths = [write(tmp_path, f"in{i}.json", d) for i, d in enumerate(datas)]
    m = ar.merge(paths)
    res = ar.judge(m, suites, partial, reason)
    return res, ar.verdict(res, partial is not None)


def statuses(res, prefix):
    return [r.status for r in res if r.id.startswith(prefix)]


# ---------------------------------------------------------------- 함수 시험


def test_full_inputs_complete(tmp_path):
    res, (_, code) = judged(tmp_path, ["fix", "nav"], full_fix(), full_nav())
    assert code == ar.EXIT_OK, [(r.id, r.status, r.evidence) for r in res if r.status not in (ar.PASS, ar.NA)]
    assert statuses(res, "NAV.table.desktop.keyboard") == [ar.NA]  # 넘치는 표가 없으면 해당 없음 (통과로 세지 않고 따로 표시)


def test_empty_and_empty_group_are_not_success(tmp_path):
    for data in ({}, {"nav": []}):
        res, (_, code) = judged(tmp_path, ["nav"], data)
        assert code == ar.EXIT_INCOMPLETE
        assert statuses(res, "COMMON.scope") == [ar.NONE]  # 범위 기록 자체가 없으면 '0건'으로 통과시키지 않음


def test_suite_is_explicit_not_inferred(tmp_path):
    res, (_, code) = judged(tmp_path, ["fix", "nav"], full_nav())  # fix 측정이 없는데 fix를 골랐으면 건너뛰지 않고 미측정
    assert code == ar.EXIT_INCOMPLETE and ar.NONE in statuses(res, "D01.")


def test_missing_required_record_is_incomplete(tmp_path):
    nav = full_nav()
    nav["summary"] = [x for x in nav["summary"] if not x["what"].startswith("Enter")]
    res, (_, code) = judged(tmp_path, ["nav"], nav)
    assert code == ar.EXIT_INCOMPLETE and statuses(res, "NAV.summary.toggle") == [ar.NONE]


def test_product_failure_wins(tmp_path):
    nav = full_nav()
    nav["summary"][0]["tabindex_attr"] = "-1"  # 초점 복귀가 summary를 Tab 순서에서 뺌
    nav["summary"][0]["tabIndex"] = -1
    res, (_, code) = judged(tmp_path, ["nav"], nav)
    assert code == ar.EXIT_FAIL and statuses(res, "NAV.summary.tabindex") == [ar.FAIL]


def test_measurement_error_is_reported_separately(tmp_path):
    nav = full_nav()
    nav["error"] = [{"check": "history", "error": "TimeoutError"}]
    res, (_, code) = judged(tmp_path, ["nav"], nav)
    assert code == ar.EXIT_INCOMPLETE and statuses(res, "COMMON.measure_error") == [ar.MERR]


def test_merge_keeps_error_and_blocked_from_earlier_files(tmp_path):
    a = full_nav()
    a["error"] = [{"check": "stale", "error": "앞 파일 오류"}]
    a["blocked_out_of_scope"] = ["http://a.example/"]
    b = {"meta": a["meta"], "tables": a["tables"], "error": None, "blocked_out_of_scope": []}
    res, (_, code) = judged(tmp_path, ["nav"], a, b)
    assert code == ar.EXIT_FAIL  # 범위 밖 요청 1건 = 실패
    assert any("앞 파일 오류" in r.evidence for r in res) and statuses(res, "COMMON.scope") == [ar.FAIL]


def test_string_error_is_preserved_and_flagged(tmp_path):
    nav = full_nav()
    nav["error"] = "문자열로 남은 오류"
    res, (_, code) = judged(tmp_path, ["nav"], nav)
    assert code == ar.EXIT_INCOMPLETE
    assert any("문자열로 남은 오류" in r.evidence for r in res if r.status == ar.MERR) and ar.IERR in statuses(res, "COMMON.input")


def test_wrong_group_type_is_input_error(tmp_path):
    nav = full_nav()
    nav["summary"] = {"what": "객체 하나"}
    res, (_, code) = judged(tmp_path, ["nav"], nav)
    assert code == ar.EXIT_INCOMPLETE and ar.IERR in statuses(res, "COMMON.input")


def test_sources_need_reuse_reason(tmp_path):
    a, b = full_fix(), full_nav()
    b["meta"]["frontend_asset"] = "index-OTHER.js"
    res, (_, code) = judged(tmp_path, ["fix", "nav"], a, b)
    assert code == ar.EXIT_INCOMPLETE and statuses(res, "COMMON.sources") == [ar.IERR]
    res, (_, code) = judged(tmp_path, ["fix", "nav"], a, b, reason="같은 소스, 시험 순서 때문에 이미지가 다름")
    assert code == ar.EXIT_OK


def test_list_rules():
    assert ar.items(" nav , nav ,,fix ") == ["nav", "fix"]  # 앞뒤 공백 제거, 빈 항목 버림, 중복은 처음 것만
    for empty in ("", ",", " ", " , ,", ",,"):
        assert ar.items(empty) == []


def test_no_criteria_is_never_complete(tmp_path):
    # 고른 기준이 0개면 범위 밖 요청 0건만으로 '전체 완료'가 되지 않는다 (함수를 직접 부르는 경우도)
    for suites, partial in (([], None), (["nav"], [])):
        res, (_, code) = judged(tmp_path, suites, SCOPE_ONLY, partial=partial)
        assert code == ar.EXIT_INCOMPLETE, [(r.id, r.status) for r in res]
        assert statuses(res, "COMMON.no_criteria") == [ar.IERR]


def test_transition_full_pass_and_required_runs(tmp_path):
    res, (_, code) = judged(tmp_path, ["transition"], full_transition())
    assert code == ar.EXIT_OK, [(r.id, r.status, r.evidence) for r in res if r.status != ar.PASS]
    assert len([r for r in res if r.suite == "transition"]) == 2 * len(TR_RUNS)  # 실행마다 정지 상태·전환 중 따로


def test_transition_active_below_need_fails(tmp_path):
    d = full_transition()
    d["transition"][0] = tr(TR_RUNS[0], low=4.4999)  # 반올림하면 4.5지만 원시값은 미달
    res, (_, code) = judged(tmp_path, ["transition"], d)
    assert code == ar.EXIT_FAIL
    assert statuses(res, f"TR.{TR_RUNS[0]}.moving") == [ar.FAIL] and statuses(res, f"TR.{TR_RUNS[0]}.static") == [ar.PASS]


def test_transition_large_text_uses_3_to_1(tmp_path):
    d = full_transition()
    d["transition"][0] = tr(TR_RUNS[0], low=3.2, size="24px", weight="400")
    d["transition"][1] = tr(TR_RUNS[1], low=3.2, size="18.66px", weight="600")  # 굵지 않은 18.66px는 일반 글자 (4.5)
    res, (_, code) = judged(tmp_path, ["transition"], d)
    assert statuses(res, f"TR.{TR_RUNS[0]}.moving") == [ar.PASS] and statuses(res, f"TR.{TR_RUNS[1]}.moving") == [ar.FAIL]


def test_transition_unobserved_or_missing_is_not_pass(tmp_path):
    d = full_transition()
    d["transition"][0] = tr(TR_RUNS[0], observed=False)  # 비활성→활성을 관측하지 못함
    d["transition"][1] = tr(TR_RUNS[1], final_disabled=True)  # 비활성으로 끝남: 정지 상태도 판정 불가
    del d["transition"][2]
    res, (_, code) = judged(tmp_path, ["transition"], d)
    assert code == ar.EXIT_INCOMPLETE
    assert statuses(res, f"TR.{TR_RUNS[0]}.moving") == [ar.NONE] and statuses(res, f"TR.{TR_RUNS[0]}.static") == [ar.PASS]
    assert statuses(res, f"TR.{TR_RUNS[1]}.static") == [ar.NONE]
    assert statuses(res, f"TR.{TR_RUNS[2]}.") == [ar.NONE, ar.NONE]


def test_transition_old_format_is_not_judged(tmp_path):
    d = full_transition()
    old = d["transition"][0]
    del old["enabled_index"], old["frames_complete"]  # 5차 판 기록: 앞 40프레임만, 첫 활성 인덱스 없음
    d["transition"][1]["frames"] = d["transition"][1]["frames"][:2]  # 저장된 프레임 수가 frames_total과 다름
    res, (_, code) = judged(tmp_path, ["transition"], d)
    assert code == ar.EXIT_INCOMPLETE
    for r in TR_RUNS[:2]:
        assert statuses(res, f"TR.{r}.moving") == [ar.NONE]
    assert statuses(res, f"TR.{TR_RUNS[0]}.static") == [ar.PASS]  # 정지 상태는 마지막 프레임 값이라 그대로 판정


def test_transition_inconsistent_record_is_input_error(tmp_path):
    d = full_transition()
    d["transition"][0]["enabled_index"] = 0  # 첫 프레임은 비활성 — 인덱스가 기록과 맞지 않음
    d["transition"][1]["enabled_below_need"] = 3  # 요약과 프레임 재계산이 다름
    res, (_, code) = judged(tmp_path, ["transition"], d)
    assert code == ar.EXIT_INCOMPLETE
    assert statuses(res, f"TR.{TR_RUNS[0]}.moving") == [ar.IERR] and statuses(res, f"TR.{TR_RUNS[1]}.moving") == [ar.IERR]


def test_partial_is_distinct_from_full(tmp_path):
    res, (title, code) = judged(tmp_path, ["nav"], full_nav(), partial=["NAV.summary.tabindex", "NAV.forward"])
    assert code == ar.EXIT_PARTIAL and "부분" in title and len([r for r in res if r.suite == "nav"]) == 2


# ---------------------------------------------------------------- CLI 종료 코드 시험 (실제 명령으로)


def test_cli_full(tmp_path):
    p = run_cli("--suite", "fix,nav", "--md", tmp_path / "r.md", write(tmp_path, "f.json", full_fix()), write(tmp_path, "n.json", full_nav()))
    assert p.returncode == 0, p.stdout[-800:]
    assert "전체 완료" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_cli_partial(tmp_path):
    p = run_cli("--suite", "nav", "--partial", "NAV.forward", write(tmp_path, "n.json", full_nav()))
    assert p.returncode == 3 and "부분 검사" in p.stdout


def test_cli_empty_and_missing(tmp_path):
    assert run_cli("--suite", "nav", write(tmp_path, "e.json", {})).returncode == 2
    nav = full_nav()
    del nav["stale"]
    assert run_cli("--suite", "nav", write(tmp_path, "m.json", nav)).returncode == 2


def test_cli_error_and_bad_json(tmp_path):
    nav = full_nav()
    nav["error"] = [{"check": "tables", "error": "Timeout"}]
    assert run_cli("--suite", "nav", write(tmp_path, "err.json", nav)).returncode == 2
    assert run_cli("--suite", "nav", write(tmp_path, "bad.json", "{ 깨진 JSON")).returncode == 2


def test_cli_failure(tmp_path):
    nav = full_nav()
    nav["nav"][0]["after_enter"] = el("H1", "홈", hidden=True)  # 숨긴 홈의 제목을 잡음
    p = run_cli("--suite", "nav", write(tmp_path, "f.json", nav))
    assert p.returncode == 1 and "실패(제품 결함) 있음" in p.stdout  # 1은 판정 결과여야 한다 (판정기가 죽어서 나온 1이 아님)


def test_cli_tool_error_is_not_product_failure(tmp_path):
    p = run_cli("--suite", "nav", "--md", tmp_path, write(tmp_path, "n.json", full_nav()))  # 보고서 경로가 폴더 → 쓰기 실패
    assert p.returncode == 4 and "Traceback" in p.stderr


def test_cli_merge_keeps_earlier_error(tmp_path):
    a = full_nav()
    a["error"] = [{"check": "summary", "error": "앞 파일 오류"}]
    b = {"meta": a["meta"], "summary": copy.deepcopy(a["summary"]), "error": [], "blocked_out_of_scope": []}
    p = run_cli("--suite", "nav", write(tmp_path, "a.json", a), write(tmp_path, "b.json", b))
    assert p.returncode == 2 and "앞 파일 오류" in p.stdout


def test_cli_empty_suite_is_usage_error(tmp_path):
    f = write(tmp_path, "scope.json", SCOPE_ONLY)
    for value in ("", ",", " ", " , ,"):
        p = run_cli("--suite", value, f)
        assert p.returncode == 5, (value, p.returncode, p.stdout[-300:])
    for value in ("", ","):
        assert run_cli("--suite", "nav", "--partial", value, f).returncode == 5


def test_cli_suite_list_is_normalized(tmp_path):
    fx, nv = write(tmp_path, "f.json", full_fix()), write(tmp_path, "n.json", full_nav())
    assert run_cli("--suite", " nav , nav ", nv).returncode == 0  # = nav
    assert run_cli("--suite", "fix,,nav,", fx, nv).returncode == 0  # = fix,nav
    assert run_cli("--suite", "nav", "--partial", " NAV.forward ,NAV.forward", nv).returncode == 3
    assert run_cli("--suite", "nav", write(tmp_path, "s.json", SCOPE_ONLY)).returncode == 2  # 측정 묶음 없음 → 불완전


def test_cli_transition(tmp_path):
    assert run_cli("--suite", "transition", write(tmp_path, "t.json", full_transition())).returncode == 0
    low = full_transition()
    low["transition"][3] = tr(TR_RUNS[3], low=1.022)
    assert run_cli("--suite", "transition", write(tmp_path, "low.json", low)).returncode == 1
    unseen = full_transition()
    unseen["transition"][3] = tr(TR_RUNS[3], observed=False)
    assert run_cli("--suite", "transition", write(tmp_path, "unseen.json", unseen)).returncode == 2
    p = run_cli("--suite", "fix,nav,transition", write(tmp_path, "f.json", full_fix()), write(tmp_path, "n.json", full_nav()), write(tmp_path, "t2.json", full_transition()))
    assert p.returncode == 0, p.stdout[-800:]


def test_cli_usage_errors(tmp_path):
    f = write(tmp_path, "n.json", full_nav())
    assert run_cli("--suite", "unknown", f).returncode == 5
    assert run_cli("--suite", "nav", "--partial", "D01.name", f).returncode == 5  # 고른 묶음에 없는 기준
    assert run_cli(f).returncode == 5  # --suite 없음 (범위를 추정하지 않음)
