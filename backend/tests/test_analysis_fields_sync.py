"""화면의 데이터 통계 분석 진입 항목(frontend/src/analysisFields.ts)이 서버 MEASURE_FIELDS와 같은지.

메일 지표가 서버에만 추가돼 확정한 메일 열에서 '이 항목으로 데이터 통계 분석'이 보이지 않던 일이 있었다 (PR #21 뒤).
frontend 소스가 없는 작업 트리(backend만 들어 있는 이미지 등)에서는 건너뛴다.
"""

import re
from pathlib import Path

import pytest

from app.analysis import MEASURE_FIELDS

TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "analysisFields.ts"


@pytest.mark.skipif(not TS.exists(), reason="frontend 소스가 없는 작업 트리")
def test_frontend_analysis_fields_match_the_server() -> None:
    text = TS.read_text(encoding="utf-8")
    block = text[text.index("ANALYSIS_FIELDS") : text.index("])")]
    assert set(re.findall(r"'([a-z0-9_]+)'", block)) == set(MEASURE_FIELDS)
