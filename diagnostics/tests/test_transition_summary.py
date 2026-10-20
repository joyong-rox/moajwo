"""transition_summary.summarize 시험 — 기록기가 넘기는 모양의 합성 프레임으로, 첫 활성 프레임 포함·비활성 제외·관측 실패를 본다.

판정기(a11y_regress)의 전환 중 기준도 같은 기록으로 함께 확인한다. 브라우저·네트워크 없음.
"""

import a11y_regress as ar
import transition_summary as ts

GREY, PALE = "rgb(156, 163, 175)", "rgb(243, 244, 246)"  # 비활성 글자·배경 (2.307:1)
MID_FG, MID_BG = "rgb(221, 223, 227)", "rgb(110, 132, 247)"  # 5차 수정 전 실제 기록의 전환 중간 프레임 (2.496:1)
WHITE, BLUE = "rgb(255, 255, 255)", "rgb(55, 88, 249)"  # 활성 (5.349:1)


def frame(t_raw, disabled, fg, bg):
    return {"t": round(t_raw), "t_raw": t_raw, "disabled": disabled, "hover": False, "focused": False, "focus_visible": False, "font_size": "16px",
            "font_weight": "600", "color": fg, "bg": bg, "opacity": "1", "around": "rgb(255, 255, 255)", "text": "로그인"}  # fmt: skip


def sampled(enabled_at, first_fg=MID_FG, first_bg=MID_BG, tail=45):
    """비활성 2프레임 → 첫 활성 프레임(enabled_at) → 활성 tail프레임. 기록기처럼 enabled_index를 남긴다."""
    frames = [frame(enabled_at - 33.4, True, GREY, PALE), frame(enabled_at - 16.7, True, GREY, PALE), frame(enabled_at, False, first_fg, first_bg)]
    frames += [frame(enabled_at + 16.7 * (i + 1), False, WHITE, BLUE) for i in range(tail)]
    return {"frames": frames, "saw_disabled": True, "enabled_at": enabled_at, "enabled_index": 2}


def summary(data, run="login.ctl.desktop.mouse_stay"):
    return ts.summarize(run, "로그인 (.mapping-run)", "합성", "desktop", "mouse_stay", data)


def moving(rec):
    crit = next(c for c in ar.CRITS if c.id == f"TR.{rec['run']}.moving")
    return crit.fn({"transition": [rec]})


def test_first_enabled_frame_only_below_fails():
    s = summary(sampled(100.4))  # 표시용 t는 100으로 내려가지만 첫 활성 프레임이 빠지면 안 된다
    assert s["first_enabled"]["t"] == 100 and s["enabled_index"] == 2
    assert s["min_enabled_ratio"] < 4.5 and s["enabled_below_need"] == 1
    status, ev = moving(s)
    assert status == ar.FAIL, ev


def test_rounding_direction_does_not_matter():
    for at in (100.4, 100.5, 100.6, 99.9999):
        s = summary(sampled(at))
        assert s["enabled_below_need"] == 1 and s["first_enabled"]["t_raw"] == at, at


def test_disabled_frames_are_excluded():
    data = sampled(100.4, first_fg=WHITE, first_bg=BLUE)
    data["frames"].append(frame(900.0, True, GREY, PALE))  # 활성 뒤 다시 비활성이 된 프레임도 예외
    s = summary(data)
    assert s["enabled_below_need"] == 0 and s["min_enabled_ratio"] > 5.3
    assert moving(s)[0] == ar.PASS


def test_unobserved_transition_is_not_measured():
    never_disabled = sampled(100.4)
    never_disabled.update(saw_disabled=False, enabled_at=None, enabled_index=None)
    never_enabled = sampled(100.4)
    never_enabled.update(enabled_at=None, enabled_index=None)
    for data in (never_disabled, never_enabled):
        s = summary(data)
        assert not s["observed"] and s["enabled_index"] is None and s["min_enabled_ratio"] is None
        assert moving(s)[0] == ar.NONE


def test_all_enabled_frames_pass():
    s = summary(sampled(100.4, first_fg=WHITE, first_bg=BLUE))
    assert s["enabled_below_need"] == 0 and moving(s)[0] == ar.PASS


def test_all_frames_are_kept():
    s = summary(sampled(540.2, tail=60))  # 5차 판은 앞 40프레임만 저장해 다시 계산할 수 없었다
    assert s["frames_complete"] and s["frames_total"] == len(s["frames"]) == 63
