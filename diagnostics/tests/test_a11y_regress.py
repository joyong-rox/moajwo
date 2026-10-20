"""a11y_regress 판정 시험 — 네트워크·브라우저 없이, 측정 JSON 모양의 작은 자료로 판정만 본다."""

import copy

import a11y_regress as ar


def live(*texts, role="status", where="파일 업로드 sr-only"):
    return [{"role": role, "where": where, "text": t} for t in texts]


START, DONE = "파일 1개 업로드를 시작했습니다.", "업로드를 마쳤습니다: 이번 요청 1개 중 성공 1개, 실패 0개."
GOOD = {
    "D-01": [{"what": "업로드 조작 요소의 계산된 이름·설명", "role": "button", "name": "파일 선택", "description": "… xlsx, xls, csv …"}],
    "A1": [
        {"what": "업로드 중 (시험에서 요청을 붙잡음)", "visible_text": "파일 선택", "aria_disabled": "true", "focused": True, "ax": {"name": "파일 선택"}, "describedby": ["업로드·분석 중…"]},
        {"what": "업로드 중 다시 눌렀을 때 파일 선택 창", "opened": {"click_zone": False, "enter": False}},
    ],
    "D-02": [
        {"what": "① 성공 1개", "live": live(START, DONE), "focus_kept": True},
        {"what": "⑦ 업로드 중 → 완료", "live": live(START, DONE)},
        {"what": "⑤ 요청 전체 실패", "live": live(START, "") + live("업로드 실패", role="alert", where="파일 업로드 wk-banner")},
    ],
    "inputs": [{"page": "묶기", "rule": ".bd-input", "ratio_vs_outside": 4.21, "ratio_vs_inside": 4.54, "outside_bg": "rgb(255, 248, 197)"}],
    "dialog": [
        {"dialog": "자동화 삭제", "attrs": {"describedby": "_r_2_", "description": ["본문"], "ancestors_with_describedby": [], "title": ["제목"]},
         "ax": {"name": "제목"}, "first_focus": {"name": "취소", "in_dialog": True}, "tab": [{"name": "삭제", "in_dialog": True}],
         "shift_tab": [{"name": "취소", "in_dialog": True}], "escape": {"closed": True, "focus_on_trigger": True}, "cancel": {"closed": True, "focus_on_trigger": True},
         "reopen_attrs": {"describedby": "_r_2_"}},
    ],
    "blocked_out_of_scope": [],
}  # fmt: skip


def status_of(results, rule_part):
    return [r.status for r in results if rule_part in r.rule]


def test_good_record_passes_and_missing_is_not_counted_as_pass():
    res = ar.judge(GOOD)
    assert ar.FAIL not in [r.status for r in res]
    assert status_of(res, "역할 button") == [ar.PASS]
    assert status_of(res, ".bd-input") == [ar.PASS]
    assert status_of(res, ".mapping-pick select") == [ar.NONE]  # 화면에 안 나온 규칙은 미측정 (같은 토큰이어도 통과 아님)
    assert status_of(res, "끌어놓기") == [ar.NONE]


def test_name_without_visible_label_fails():
    bad = copy.deepcopy(GOOD)
    bad["D-01"][0]["name"] = "xlsx, xls, csv 파일을 끌어다 놓거나 눌러서 선택"
    assert status_of(ar.judge(bad), "역할 button") == [ar.FAIL]


def test_busy_without_disabled_state_or_picker_opening_fails():
    bad = copy.deepcopy(GOOD)
    bad["A1"][0]["aria_disabled"] = None
    bad["A1"][1]["opened"]["enter"] = True
    res = ar.judge(bad)
    assert status_of(res, "aria-disabled=true") == [ar.FAIL]
    assert status_of(res, "열리지 않음") == [ar.FAIL]


def test_duplicate_or_missing_announcements_fail():
    bad = copy.deepcopy(GOOD)
    bad["D-02"][0]["live"] = live(DONE)  # 시작 안내 누락
    bad["D-02"][1]["live"] = live(START, DONE) + live("검증 대상 0개 · 전체 9개", where="통합 전 검증 wk-pickcount")  # 목록 갱신으로 고른 개수 재전달
    bad["D-02"][2]["live"] = live(START, DONE) + live("업로드 실패", role="alert")  # 전체 실패인데 결과 안내
    res = ar.judge(bad)
    assert status_of(res, "① 성공") == [ar.FAIL]
    assert status_of(res, "⑦ 완료 뒤") == [ar.FAIL]
    assert status_of(res, "⑤ 요청 전체 실패") == [ar.FAIL]


def test_low_contrast_input_fails_with_raw_value():
    bad = copy.deepcopy(GOOD)
    bad["inputs"][0]["ratio_vs_outside"] = 2.9999  # 소수 둘째 자리 반올림이면 3.00으로 보이는 값
    assert status_of(ar.judge(bad), ".bd-input") == [ar.FAIL]


def test_dialog_description_on_backdrop_or_focus_escape_fails():
    bad = copy.deepcopy(GOOD)
    bad["dialog"][0]["attrs"]["describedby"] = None
    bad["dialog"][0]["attrs"]["ancestors_with_describedby"] = ["DIV.fixed"]
    bad["dialog"][0]["tab"].append({"name": "홈", "in_dialog": False})
    res = ar.judge(bad)
    assert status_of(res, "aria-describedby가 본문") == [ar.FAIL]
    assert status_of(res, "Tab·Shift+Tab") == [ar.FAIL]


def test_axe_not_run_is_not_a_pass():
    data = copy.deepcopy(GOOD)
    data["A1"].append({"what": "axe label-content-name-mismatch — 홈 업로드 영역", "axe": "미실행 (AXE_JS 없음)"})
    assert status_of(ar.judge(data), "axe label-content-name-mismatch") == [ar.NONE]


def el(tag, text=None, **kw):
    return {"where": "element", "tag": tag, "text": text, "key": kw.get("key"), "cls": kw.get("cls", ""), "in_hidden": kw.get("hidden", False), "in_main": True, "in_dialog": False}


NAV = {
    "nav": [
        {"what": "본문 바로가기 — 홈", "first_tab": el("A", "본문으로 바로가기", cls="skip-link"), "visible_when_focused": True, "after_enter": el("H1", "홈"), "url_unchanged": True},
        {"what": "상세 → 목록 ('검토 목록으로')", "opener_key": "review-row-9", "after": el("BUTTON", "검토 보기", key="review-row-9")},
    ],
    "tables": [{"viewport": "mobile", "tables": [{"label": "결과 표", "role": "region", "tabindex": "0", "scrolls": True, "th_col": 8},
                                                 {"label": None, "role": None, "tabindex": None, "scrolls": False, "th_col": 4}],
                "keyboard_scroll": {"before": [0, 0], "after": [40, 0]}}],
    "blocked_out_of_scope": [],
}  # fmt: skip


def test_nav_only_record_is_judged_without_fix_check_noise():
    res = ar.judge(NAV)
    assert not [r for r in res if r.item.startswith("D-") or r.item == "확인창"]  # D 항목 측정이 없으면 그 기준을 섞지 않는다
    assert ar.FAIL not in [r.status for r in res]
    assert status_of(res, "메뉴 → ") == []  # 이동 묶음 안에서 빠진 측정은 판정 줄을 만들지 않거나 미측정 (통과로 세지 않음)
    assert ar.NONE in [r.status for r in res]  # 예: 검토 목록 → 상세 측정이 없음


def test_skip_link_to_hidden_home_heading_fails():
    bad = copy.deepcopy(NAV)
    bad["nav"][0]["after_enter"] = el("H1", "홈", hidden=True)  # 숨긴 홈의 제목을 잡음
    assert status_of(ar.judge(bad), "본문 바로가기(홈)") == [ar.FAIL]


def test_return_to_list_without_opener_fails():
    bad = copy.deepcopy(NAV)
    bad["nav"][1]["after"] = {"where": "(본문)"}
    assert status_of(ar.judge(bad), "상세 → 목록") == [ar.FAIL]


def test_table_tab_stop_only_when_scrolling():
    bad = copy.deepcopy(NAV)
    bad["tables"][0]["tables"][0]["tabindex"] = None  # 넘치는데 초점 불가
    bad["tables"][0]["tables"][1]["tabindex"] = "0"  # 넘치지 않는데 정지점
    assert status_of(ar.judge(bad), "넘치는 영역만") == [ar.FAIL]


def test_merge_keeps_out_of_scope_requests_and_errors_from_every_file(tmp_path):
    import json

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"D-01": [{"what": "x"}], "blocked_out_of_scope": ["http://a.example/"], "error": [{"check": "upload"}]}), encoding="utf-8")
    b.write_text(json.dumps({"nav": [], "blocked_out_of_scope": [], "error": None}), encoding="utf-8")
    data = ar.merge([a, b])
    assert data["blocked_out_of_scope"] == ["http://a.example/"]
    assert data["error"] == [{"check": "upload"}]
    assert status_of(ar.judge(data), "진단 주소 밖 요청 0") == [ar.FAIL]
