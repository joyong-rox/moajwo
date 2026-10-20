"""버튼 전환 기록 집계 (button_transition_check.py가 쓰고, 시험이 브라우저 없이 그대로 부른다).

- 판정 값은 표시용 반올림과 나눈다: 프레임의 `t`는 표시용(반올림 ms), `t_raw`는 반올림하지 않은 시각이다.
- 활성 구간은 기록기가 남긴 첫 활성 프레임의 인덱스(`enabled_index`)부터다. 시각 비교를 쓰지 않으므로
  반올림 방향과 상관없이 첫 활성 프레임이 빠지지 않는다 (5차 판은 반올림한 t와 반올림하지 않은 활성 시각을 비교해 빠질 수 있었다).
- 프레임은 모두 저장한다 (`frames_complete`). 판정기는 저장된 프레임으로 다시 계산한다.
"""

import re

from contrast_calc import oklab_to_srgb, over, ratio


def color(s: str):
    p = [float(x) for x in re.findall(r"-?[\d.]+", s)]
    rgb = oklab_to_srgb(*p[:3]) if s.startswith("oklab") else tuple(p[:3])
    return rgb, (p[3] if len(p) > 3 else 1.0)


def need_of(f: dict) -> float:
    px, w = float(f["font_size"].rstrip("px")), int(float(f["font_weight"]))
    return 3.0 if px >= 24 or (px >= 18.66 and w >= 700) else 4.5


def summarize(run_id: str, button: str, kind: str, vp: str, method: str, data: dict) -> dict:
    """data: 기록기 한 선택자의 결과 {frames, saw_disabled, enabled_at, enabled_index}."""
    frames = data["frames"]
    for f in frames:
        (fg, fa), (bg, ba), (around, _) = color(f["color"]), color(f["bg"]), color(f["around"])
        op = float(f["opacity"])
        bg_on = over(bg, ba * op, around)  # 요소 투명도까지 뒤 배경과 합성
        f["ratio"] = ratio(over(fg, fa * op, bg_on), bg_on)
    idx = data.get("enabled_index")
    observed = bool(data["saw_disabled"]) and idx is not None
    enabled = [f for f in frames[idx:] if not f["disabled"]] if observed else []
    low = min(enabled, key=lambda f: f["ratio"]) if enabled else None
    last = frames[-3:] if len(frames) >= 3 else frames
    stable = len({(f["color"], f["bg"], f["opacity"]) for f in last}) == 1 and bool(last)
    final = frames[-1] if frames else None
    at = data.get("enabled_at")
    return {"run": run_id, "button": button, "kind": kind, "viewport": vp, "method": method, "observed": observed, "saw_disabled": data["saw_disabled"],
            "enabled_index": idx if observed else None, "enabled_at_raw": at, "enabled_at_ms": None if at is None else round(at), "frames_total": len(frames),
            "need": need_of(final) if final else None, "font": f"{final['font_size']} {final['font_weight']}" if final else None,
            "min_enabled_ratio": low["ratio"] if low else None, "min_enabled_frame": low, "enabled_below_need": sum(1 for f in enabled if f["ratio"] < need_of(f)),
            "first_enabled": enabled[0] if enabled else None, "final_ratio": final["ratio"] if final else None, "final_disabled": final["disabled"] if final else None,
            "static_stable": stable, "frames_complete": True, "frames": frames}  # fmt: skip
