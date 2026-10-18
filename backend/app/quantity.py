"""사용자가 수량 항목으로 확정한 열의 계산용 값.

파서는 원문을 보존한다 — 코드·번호·식별자 이름, 앞자리 0 등을 근거로 숫자 글자를 글자로 둔다
(미리보기·캐시는 원문 그대로).
사용자가 그 열을 수량 항목으로 확정했다면 검증·분석·통합은 숫자로 읽을 수 있는 글자를 그 의미대로 수량으로 계산한다.

- 확정(mapping_decisions)만 본다 — 자동 제안은 해석을 바꾸지 않는다.
- 숫자 해석은 parsing.to_number와 같다: 정수 글자는 int()로 읽어 자릿수가 바뀌지 않고, 소수 글자는 float()로 읽는다
  (이진 부동소수 — 소수 계산 정밀도는 기존 숫자 셀과 같다). 숫자로 읽을 수 없는 글자(결측·비공개 표기, 앞자리 0이 있는
  숫자 글자, 문자 등)는 그대로 둔다 — 0으로 바꾸지 않는다. 남은 값은 검증 경고·분석 제외 사유로 알린다.
- 파싱 결과·미리보기 캐시의 행을 바꾸지 않고 새 행을 만든다 (확정을 바꾸면 다음 요청부터 그대로 반영된다).

검토는 검사에 적용한 매핑(확정·자동 적용 각각)을 남기고 완료·통합 전에 지금 매핑과 비교한다 (app.check_basis) —
확정 여부가 바뀌면 이 계산용 해석도 달라지므로 이전 검사 결과로 완료·통합하지 않는다.
"""

from __future__ import annotations

from dataclasses import replace

from app.mapping import ColumnMapping
from app.parsing import Cell, SheetResult, is_missing, to_number

# 수량으로 비교·계산하는 표준 항목 (값이 '010'처럼 글자로 저장돼도 단위를 비교한다). 이름·코드·구분 항목은 넣지 않는다.
QUANTITY_FIELDS = frozenset({
    "population", "population_male", "population_female", "households", "budget", "expenditure", "execution_rate",
    "amount", "count", "ratio", "change_rate", "headcount", "vehicles_total", "passenger_car", "van", "truck",
    "special_vehicle", "motorcycle", "eco_friendly_car", "electric_car", "hydrogen_car", "hybrid_car",
    "government_use", "private_use", "commercial_use", "total", "registration_count",
})  # fmt: skip


def confirmed_quantity_indexes(mappings: list[ColumnMapping]) -> frozenset[int]:
    """사용자가 수량 항목으로 확정한 열의 위치 (자동 제안·'해당 없음'·다른 항목은 제외)."""
    return frozenset(i for i, m in enumerate(mappings) if m.status == "confirmed" and m.field_key in QUANTITY_FIELDS)


def quantity_value(cell: Cell) -> Cell:
    """확정한 수량 열의 칸 → 계산용 값. 숫자로 읽을 수 있는 글자만 숫자로 (정수 int, 소수 float), 나머지는 그대로."""
    if isinstance(cell, str):
        number = to_number(cell)
        if number is not None:
            return number
    return cell


def quantity_sheet(sheet: SheetResult, indexes: frozenset[int]) -> SheetResult:
    """확정한 수량 열의 글자 숫자를 계산용 값으로 바꾼 새 시트.

    원래 시트·행은 바꾸지 않는다 (원문은 각 행의 'original'에 그대로).
    """
    if not indexes:
        return sheet
    body = []
    for row in sheet.body:
        cells = row["cells"]
        if any(j < len(cells) and isinstance(cells[j], str) for j in indexes):
            row = {**row, "cells": [quantity_value(c) if j in indexes else c for j, c in enumerate(cells)]}
        body.append(row)
    return replace(sheet, body=body)


def text_value_columns(sheet: SheetResult, numeric_only: bool = False) -> list[str]:
    """글자 값(빈 칸·결측·비공개 표기 제외)이 남은 열 — 수량 항목으로 확정하면 검사·계산 결과가 달라지는 열.

    숫자로 읽을 수 있는 글자는 계산용 값이 되고, 읽을 수 없는 글자는 '수량으로 계산하지 못한 값' 경고가 된다.
    값이 모두 숫자인 열은 수량으로 확정해도 계산이 같으므로 넣지 않는다.
    numeric_only: 숫자로 읽을 수 있는 글자가 있는 열만 (확정하면 계산 값이 바뀌는 열).
    """
    out = []
    for j, name in enumerate(sheet.columns):
        for r in sheet.body:
            cells = r["cells"]
            if r["kind"] not in ("data", "subtotal") or j >= len(cells):
                continue
            c = cells[j]
            if not isinstance(c, str) or not c.strip() or is_missing(c):
                continue
            if not numeric_only or not isinstance(quantity_value(c), str):
                out.append(name)
                break
    return out
