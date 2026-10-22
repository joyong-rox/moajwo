"""합계 항목의 산식 확인 (D-01) — 같은 이름의 합계라도 자료마다 산식이 다를 수 있어 이름만 믿지 않고 값으로 본다.

예: 시연 자료의 통일부 '발신건수합계' 셀은 =SUM(F2:I2)로 Mail·외부 발신에 시스템·대용량 발신까지 더한 값이고,
다른 부처의 '발신건수합계'는 =E2+F2(Mail+외부)다. 이름이 같아 둘 다 '발신건수합계'(Mail+외부)로 연결되면 서로 다른
정의를 함께 더하게 된다. 차이를 입력 오류로 단정하지 않고, 어느 산식과 맞는지 근거를 보여 준 뒤 사용자가 확정한다.

- 합계 열과 구성 항목 열이 같은 시트에 모두 있을 때만 본다 (없으면 확인하지 못함 — 경고하지 않음).
- 숫자로 읽을 수 없는 칸(빈칸·비공개 표기·글자)이 있는 행은 비교하지 않는다 (0으로 보지 않음).
- 정수는 정확히, 소수는 이진 표현 오차만 허용해 비교한다.
- 읽기만 한다: 매핑·값을 바꾸지 않는다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.quantity import quantity_value

# 합계 = 구성 항목의 합. 항목 이름·단위·산식 설명은 docs/MAIL_FIELDS.md
TOTAL_FORMULAS: dict[str, tuple[str, ...]] = {
    "mail_sent_total_count": ("mail_sent_count", "mail_external_sent_count"),
    "mail_received_total_count": ("mail_received_count", "mail_external_received_count"),
    "mail_all_sent_total_count": (
        "mail_sent_count",
        "mail_external_sent_count",
        "mail_system_sent_count",
        "mail_large_file_sent_count",
    ),
}


@dataclass
class FormulaCheck:
    total: str  # 합계 항목 key
    column: int  # 합계 열 번호
    checked: int  # 모든 칸이 숫자라 비교한 행 수
    differ: list[tuple[int, float]] = field(default_factory=list)  # (원본 행, 합계 - 구성 항목 합) — 다른 행
    # 다른 행이 있을 때, 같은 시트의 열로 만들 수 있는 다른 산식 중 가장 많이 맞는 것: (항목 key, 맞는 행 수)
    alternative: tuple[str, int] | None = None

    @property
    def difference(self) -> float:
        return sum(d for _, d in self.differ)


def _number(cell: Any) -> float | None:
    value = quantity_value(cell)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _same(a: float, b: float) -> bool:
    if float(a).is_integer() and float(b).is_integer():
        return a == b
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)


def _rows(rows: Iterable[tuple[int, Sequence[Any]]], total: int, parts: list[int]) -> list[tuple[int, float, float]]:
    """(원본 행, 합계 값, 구성 항목 합) — 모든 칸이 숫자인 행만."""
    out = []
    for row_no, cells in rows:
        values = [_number(cells[j]) if j < len(cells) else None for j in (total, *parts)]
        if all(v is not None for v in values):
            out.append((row_no, values[0], sum(values[1:])))  # type: ignore[arg-type]
    return out


def check_totals(rows: Sequence[tuple[int, Sequence[Any]]], columns: dict[str, int]) -> list[FormulaCheck]:
    """합계 열마다 산식과 맞는지 본다. columns: 항목 key → 열 번호 (한 열에만 연결된 항목만 넘긴다)."""
    out = []
    for total, parts in TOTAL_FORMULAS.items():
        if total not in columns or any(p not in columns for p in parts):
            continue
        compared = _rows(rows, columns[total], [columns[p] for p in parts])
        check = FormulaCheck(total, columns[total], len(compared))
        check.differ = [(row_no, t - s) for row_no, t, s in compared if not _same(t, s)]
        if check.differ:
            best: tuple[str, int] | None = None
            for other, other_parts in TOTAL_FORMULAS.items():
                if other == total or any(p not in columns for p in other_parts):
                    continue
                alt = _rows(rows, columns[total], [columns[p] for p in other_parts])
                matched = sum(1 for _, t, s in alt if _same(t, s))
                if matched and (best is None or matched > best[1]):
                    best = (other, matched)
            check.alternative = best
        out.append(check)
    return out


def unique_columns(pairs: Iterable[tuple[str | None, int]]) -> dict[str, int]:
    """(항목 key, 열 번호) → 한 열에만 연결된 산식 관련 항목만 (같은 항목이 두 열이면 어느 열인지 알 수 없어 뺀다)."""
    found: dict[str, list[int]] = {}
    wanted = set(TOTAL_FORMULAS) | {p for parts in TOTAL_FORMULAS.values() for p in parts}
    for key, j in pairs:
        if key in wanted:
            found.setdefault(key, []).append(j)
    return {k: js[0] for k, js in found.items() if len(js) == 1}
