"""메일 지표를 다른 뜻·단위·기준일로 잘못 합산하지 않는 회귀 검사."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import analysis
from app.mapping import MappingEngine, load_dictionary
from app.mapping_review import FileView, SheetView
from app.quantity import confirmed_quantity_indexes


def engine():
    return MappingEngine(load_dictionary(), embed_fn=None, llm_fn=None)


def mapped(columns, decisions=None):
    return engine().map_columns(columns, [[1]] * len(columns), use_llm=False, decisions=decisions).columns


def evaluate(sheets, field_key, target_options=None, **kwargs):
    """저장소 경계만 대체하고 실제 매핑·분석 엔진을 실행한다. target_options: 파일 번호 → TargetIn에 더할 값."""
    views, decisions, records = {}, {}, {}
    for i, (columns, rows, overrides) in enumerate(sheets, 1):
        proposals = mapped(columns, overrides)
        decisions[i] = {"시트1": {m.column: m.field_key for m in proposals}}
        sv = SheetView(
            name="시트1", columns=columns, source_columns=list(range(len(columns))),
            header_row=1, header_row_end=1, header_cells=[columns],
            rows=[(n, "data", row) for n, row in enumerate(rows, 2)],
            data_rows=len(rows), title_texts=[], note_texts=[],
        )
        views[i] = FileView([sv])
        records[i] = SimpleNamespace(id=i, uploaded_by=1, original_name=f"{i}.xlsx", sha256=str(i))
    req = analysis.AnalysisIn(
        field_key=field_key,
        targets=[
            analysis.TargetIn(
                file_id=i, unit=None if field_key.endswith("_mb") else "건", **(target_options or {}).get(i, {})
            )
            for i in records
        ],
        **kwargs,
    )
    with (
        patch.object(analysis, "get_file_for", side_effect=lambda db, user, i: records[i]),
        patch.object(analysis, "file_view", side_effect=lambda db, rec: views[rec.id]),
        patch.object(analysis, "load_decisions", side_effect=lambda db, i: decisions[i]),
    ):
        return analysis.evaluate(None, SimpleNamespace(id=1), req)


def test_direction_totals_and_department_aliases_are_distinct():
    columns = ["발신메일건수", "외부발신건수", "총 발신 건수", "수신메일건수", "외부수신건수", "총 수신 건수"]
    assert [m.field_key for m in mapped(columns)] == [
        "mail_sent_count", "mail_external_sent_count", "mail_sent_total_count",
        "mail_received_count", "mail_external_received_count", "mail_received_total_count",
    ]
    assert [m.field_key for m in mapped(["부서명", "담당부서", "통계일자"])] == [
        "organization_name", "department", "base_date",
    ]


def test_all_new_aliases_have_one_owner():
    from app.mapping import normalize

    owners = {}
    for f in load_dictionary():
        for text in f.texts:
            owners.setdefault(normalize(text), set()).add(f.key)
    for f in load_dictionary():
        if f.key.startswith(("mail", "organization_")):
            assert all(owners[normalize(text)] == {f.key} for text in f.texts)


def test_file_override_does_not_change_other_ministries():
    assert mapped(["발신건수합계"], {"발신건수합계": "mail_all_sent_total_count"})[0].field_key == "mail_all_sent_total_count"
    assert mapped(["발신건수합계"])[0].field_key == "mail_sent_total_count"


@pytest.mark.parametrize("column", ["발신크기합계(GB)", "발신크기합계(MiB)", "발신크기합계"])
def test_capacity_does_not_silently_map_to_mb(column):
    result = mapped([column])[0]
    assert result.status == "unresolved"
    assert result.field_key is None
    assert mapped(["발신 총용량(MB)"])[0].field_key == "mail_sent_size_mb"


def test_identifiers_and_missing_values_are_not_summed_as_numbers():
    columns = ["날짜", "기관코드", "조직코드", "발신건수합계"]
    ev = evaluate([(columns, [["2026-10-20", "001", "00101", 10],
                              ["2026-10-20", "001", "00102", None]], {})], "mail_sent_total_count")
    result = analysis.compute(ev)
    assert result["rows_included"] == 1
    assert result["groups"][0]["sum"] == 10
    assert result["groups"][0]["mean"] == 10
    assert result["partial"] is True
    assert ev.targets[0].rows[0].dims["organization_code"] == "00101"
    confirmed = mapped(columns, {c: m.field_key for c, m in zip(columns, mapped(columns), strict=True)})
    assert confirmed_quantity_indexes(confirmed) == frozenset({3})


def test_explicit_mb_gb_mappings_still_require_unit_choice():
    mb = ["날짜", "조직코드", "발신크기합계(MB)"]
    gb = ["날짜", "조직코드", "발신크기합계(GB)"]
    ev = evaluate([(mb, [["2026-10-20", "001", 100]], {}),
                   (gb, [["2026-10-20", "002", 2]], {gb[-1]: "mail_sent_size_mb"})], "mail_sent_size_mb")
    assert ev.units == ["GB", "MB"]
    with pytest.raises(HTTPException):
        analysis.compute(ev)


def test_mailbox_stock_at_different_dates_is_blocked():
    columns = ["날짜", "조직코드", "전체메일함크기(MB)"]
    ev = evaluate([(columns, [["2026-10-20", "001", 10], ["2026-10-21", "002", 20]], {})], "mailbox_size_mb")
    assert ev.date_conflict
    with pytest.raises(HTTPException):
        analysis.compute(ev)


def test_mailbox_same_date_and_different_organizations_can_be_summed():
    columns = ["날짜", "조직코드", "전체메일함크기(MB)"]
    ev = evaluate([(columns, [["2026-10-20", "001", 10], ["2026-10-20", "002", 20]], {})], "mailbox_size_mb")
    assert analysis.compute(ev)["groups"][0]["sum"] == 30
