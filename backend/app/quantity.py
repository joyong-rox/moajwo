"""사용자가 수량 항목으로 확정한 열의 계산용 값.

파서는 원문을 보존한다 — 코드·번호·식별자 이름, 앞자리 0 등을 근거로 숫자 글자를 글자로 둔다
(미리보기·캐시는 원문 그대로).
사용자가 그 열을 수량 항목으로 확정했다면 검증·분석·통합은 숫자로 읽을 수 있는 글자를 그 의미대로 수량으로 계산한다.

- 확정(mapping_decisions)만 본다 — 자동 제안은 해석을 바꾸지 않는다.
- 정수 글자는 float를 거치지 않는다 (parsing.to_number). 숫자로 읽을 수 없는 글자(결측·비공개 표기, 앞자리 0이 있는
  숫자 글자, 문자 등)는 그대로 둔다 — 0으로 바꾸지 않는다. 남은 값은 검증 경고·분석 제외 사유로 알린다.
- 파싱 결과·미리보기 캐시의 행을 바꾸지 않고 새 행을 만든다 (확정을 바꾸면 다음 요청부터 그대로 반영된다).
"""

from __future__ import annotations

from dataclasses import replace

from app.mapping import ColumnMapping
from app.parsing import Cell, SheetResult, to_number

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
    """확정한 수량 열의 칸 → 계산용 값. 숫자로 읽을 수 있는 글자만 숫자로 (정수는 자릿수 그대로), 나머지는 그대로."""
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
