"""통합 전 사전 검토: 검증·매핑·자동 보정 결과를 검토 항목으로 펼치고, 승인/거부를 백엔드 상태에 반영한다.

항목 분류 (색): block 차단(빨강) / pending 확인 필요(노랑, 승인·거부) / auto 자동 적용(회색) / warning 검증 경고(참고)
근거 문장: 규칙 판정은 고정 템플릿 문장(reason_source='rule'), LLM 판정만 LLM이 생성한 문장(reason_source='llm').
원본 파일과 파싱 결과는 바꾸지 않는다. 승인/거부는 매핑 확정·개인정보 확인·세션 선택값으로만 기록한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.decisions import DecisionInput, learn_column, load_decisions, load_learned, save_decisions
from app.files import load_parsed
from app.glossary import normalize_name
from app.glossary_api import get_glossary
from app.mapping import ColumnMapping, MappingEngine, get_engine
from app.models import GlossaryAlias, ReviewItem, ReviewSession, SourceFile
from app.parsing import FileResult, SheetResult
from app.pii import PII_LABEL
from app.validation import (
    EntityResolver,
    FileInput,
    MappedColumn,
    SheetInput,
    ValidationParams,
    ValidationResult,
    row_entity_names,
    validate,
)
from app.validation_api import save_run

ORDER = {"block": 0, "pending": 1, "auto": 2, "warning": 3}
BLOCK_PENALTY, PENDING_PENALTY, WARNING_PENALTY, WARNING_PENALTY_MAX = 25, 5, 2, 10
BLOCKED_SCORE_CAP = 59


class ReviewError(Exception):
    pass


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _rows(rows: list[int], total: int) -> str:
    shown = ", ".join(str(r) for r in rows)
    return f"행 {shown}{' …' if total > len(rows) else ''}"


# ---------------------------------------------------------------- 근거 문장 (규칙 템플릿)


def mapping_reason(m: ColumnMapping) -> tuple[str, str]:
    """(근거 문장, 출처). LLM 판정만 LLM 문장을 쓰고, 나머지는 템플릿."""
    label = m.field_label or ""
    top = m.candidates[0] if m.candidates else None
    second = m.candidates[1] if len(m.candidates) > 1 else None
    if m.method == "llm":
        text = (m.reason or "").removeprefix("LLM 제안: ").removeprefix("LLM도 맞는 항목을 찾지 못함: ").strip()
        if m.status == "unresolved":
            text = f"LLM도 맞는 표준 항목을 찾지 못했습니다. {text}".strip()
        return text or "LLM이 근거를 남기지 않았습니다.", "llm"
    if m.method == "user":
        if m.field_key:
            return f"사용자가 '{m.column}' 열을 '{label}'(으)로 확정했습니다.", "rule"
        return f"사용자가 '{m.column}' 열을 '해당 없음'으로 확정했습니다.", "rule"
    if m.method == "exact":
        return f"열 이름 '{m.column}'이(가) 표준 사전의 '{label}'과(와) 일치합니다 (공백·단위 괄호·연도 제외).", "rule"
    if m.method == "learned":
        return f"이전에 사용자가 확정해 학습된 열 이름 '{m.column}' → '{label}'입니다.", "rule"
    if m.method == "exact_part":
        return f"2단 헤더 열 이름의 일부가 표준 사전의 '{label}'과(와) 일치해 확인이 필요합니다.", "rule"
    if m.method == "pattern":
        return f"샘플 값이 모두 행정동 코드표에 있는 코드라 '{label}'(으)로 판단했습니다. ({m.reason})", "rule"
    if m.method == "similarity" and top is not None:
        detail = f"문자열 {top.fuzzy:.2f}" + (f", 의미 {top.embedding:.2f}" if top.embedding is not None else "")
        gap = f", 2순위 '{second.label}' {second.score:.2f}" if second else ""
        if m.status == "auto":
            return (
                f"유사도 {top.score:.2f}({detail})로 자동 적용 기준 0.90 이상이고 후보 차이가 충분합니다{gap}.",
                "rule",
            )
        return (
            f"유사도 {top.score:.2f}({detail})로 확인 기준(0.75 이상, 0.90 미만 또는 후보 차이 작음)입니다{gap}.",
            "rule",
        )
    if top is not None:
        return (
            f"표준 사전과 맞는 항목을 찾지 못했습니다. 가장 가까운 후보는 '{top.label}' {top.score:.2f}입니다.",
            "rule",
        )
    return "표준 사전과 맞는 항목을 찾지 못했습니다.", "rule"


def entity_reason(status: str, value: str, note: str | None, candidates: list[dict[str, Any]]) -> str:
    first = candidates[0] if candidates else None
    target = f"'{first['name']}'({first['code']})" if first else None
    if status == "unknown":
        tail = (
            f" 이름이 가장 비슷한 항목은 {target}입니다."
            if target
            else " 비슷한 항목이 없어 용어사전 등록이 필요합니다."
        )
        return f"'{value}'은(는) 용어사전에 없는 이름입니다.{tail}"
    if status == "ambiguous":
        names = ", ".join(f"{c['name']}({c['code']})" for c in candidates[:3])
        return f"'{value}'과(와) 같은 이름의 항목이 여러 개라 하나로 정할 수 없습니다. 후보: {names}."
    tail = f" 개편 후 항목 후보는 {target}입니다." if target else ""
    return f"{note or f'{value}은(는) 기준일에 유효하지 않은 항목입니다.'}{tail}"


# ---------------------------------------------------------------- 세션 만들기


@dataclass
class _Loaded:
    record: SourceFile
    parsed: FileResult
    mappings: dict[str, list[ColumnMapping]]  # 시트 → 열 매핑


def _load(db: Session, file_id: int, engine: MappingEngine, learned: dict[str, str], use_llm: bool) -> _Loaded:
    record, parsed = load_parsed(db, file_id)
    decisions = load_decisions(db, file_id)
    mappings = {
        sheet.name: engine.map_columns(
            sheet.columns, sheet.samples, use_llm=use_llm, learned=learned, decisions=decisions.get(sheet.name)
        ).columns
        for sheet in parsed.sheets
    }
    return _Loaded(record, parsed, mappings)


def _file_input(loaded: _Loaded) -> FileInput:
    record = loaded.record
    sheets = [
        SheetInput(
            sheet,
            [
                MappedColumn(i, c.column, c.field_key, c.qualifier)
                for i, c in enumerate(loaded.mappings[sheet.name])
                if c.field_key and c.status in ("confirmed", "auto")
            ],
        )
        for sheet in loaded.parsed.sheets
    ]
    summary = json.loads(record.pii_summary) if record.pii_summary else {"counts": {}, "items": []}
    return FileInput(
        record.id,
        record.original_name,
        sheets,
        summary["counts"],
        summary["items"],
        record.pii_acknowledged_at is not None,
    )


def create_session(db: Session, params: dict[str, Any], use_llm: bool = True) -> ReviewSession:
    """검토 세션을 만든다. params: file_ids, baseline_file_ids, expected_period, required_entities, entity_field,
    change_threshold (검증 API와 같은 조건)."""
    engine = get_engine()
    learned = load_learned(db)
    targets = [_load(db, fid, engine, learned, use_llm) for fid in dict.fromkeys(params["file_ids"])]
    baseline = [_load(db, fid, engine, learned, False) for fid in dict.fromkeys(params.get("baseline_file_ids", []))]
    vparams = ValidationParams(
        expected_period=params.get("expected_period"),
        required_entities=params.get("required_entities", []),
        entity_field=params.get("entity_field"),
        change_threshold=params.get("change_threshold", 0.5),
    )
    glossary = get_glossary(db)
    files = [_file_input(t) for t in targets]
    result = validate(files, vparams, [_file_input(b) for b in baseline], glossary=glossary)
    run = save_run(db, json.dumps(params, ensure_ascii=False), result)

    session = ReviewSession(
        status="in_review",
        params=json.dumps(params, ensure_ascii=False),
        file_ids=json.dumps([t.record.id for t in targets]),
        excluded_file_ids="[]",
        validation_run_id=run.id,
        remember_format=False,
    )
    db.add(session)
    db.flush()
    items = _validation_items(result) + _pii_items(targets)
    for loaded in targets:
        items += _mapping_items(loaded)
        items += _correction_items(loaded)
    labels = {f.key: f.label for f in engine.fields}
    items += _entity_items(result, files, EntityResolver(glossary, vparams.expected_period), labels)
    for item in items:
        item.session_id = session.id
        db.add(item)
    db.commit()
    return session


def _item(category: str, kind: str, reason: str, *, source: str = "rule", **fields: Any) -> ReviewItem:
    payload = fields.pop("payload", {})
    return ReviewItem(
        category=category,
        kind=kind,
        reason=reason,
        reason_source=source,
        payload=json.dumps(payload, ensure_ascii=False, default=str),
        **fields,
    )


def _validation_items(result: ValidationResult) -> list[ReviewItem]:
    items = []
    for b in result.blocks:
        before, after = {
            "sum_mismatch": (_fmt(b.actual), f"{_fmt(b.expected)} (하위 행 합계)"),
            "required_entity_missing": ("(자료에 없음)", str(b.expected)),
            "period_mismatch": (str(b.actual), f"{b.expected} (기준 기간)"),
        }[b.code]
        items.append(
            _item(
                "block",
                b.code,
                f"{b.message} 원본 파일을 고친 뒤 다시 검사해야 통합할 수 있습니다.",
                file_id=b.file_id,
                file_name=b.file_name,
                sheet=b.sheet,
                row=b.row,
                column=b.column,
                before=before,
                after=after,
            )
        )
    for w in result.warnings:
        items.append(
            _item(
                "warning",
                w.code,
                w.message,
                file_id=w.file_id,
                file_name=w.file_name,
                sheet=w.sheet,
                row=w.row,
                column=w.column,
                before=_fmt(w.expected) if w.expected is not None else None,
                after=_fmt(w.actual) if w.actual is not None else None,
            )
        )
    return items


def _pii_items(targets: list[_Loaded]) -> list[ReviewItem]:
    items = []
    for t in targets:
        summary = json.loads(t.record.pii_summary) if t.record.pii_summary else {}
        counts = summary.get("counts", {})
        if not any(counts.values()):
            continue
        found = ", ".join(f"{PII_LABEL[k]} {v}건" for k, v in counts.items() if v)  # type: ignore[index]
        first = summary.get("items", [{}])[0]
        where = f" (위치: {first.get('sheet')} {first.get('column')}{first.get('row')} 외)" if first else ""
        acknowledged = t.record.pii_acknowledged_at is not None
        reason = (
            f"{found}이(가) 발견됐습니다{where}. 승인하면 개인정보 포함을 확인한 것으로 기록하고 통합에 포함합니다. "
            "거부하면 이 파일을 이번 통합에서 제외합니다."
        )
        items.append(
            _item(
                "auto" if acknowledged else "pending",
                "pii",
                "이전에 개인정보 포함을 확인한 파일입니다." if acknowledged else reason,
                file_id=t.record.id,
                file_name=t.record.original_name,
                before=f"개인정보 의심 {sum(counts.values())}건",
                after="포함 확인됨" if acknowledged else "포함 확인 후 통합",
                decision="approved" if acknowledged else None,
            )
        )
    return items


def _mapping_items(loaded: _Loaded) -> list[ReviewItem]:
    items = []
    for sheet in loaded.parsed.sheets:
        for m in loaded.mappings[sheet.name]:
            reason, source = mapping_reason(m)
            label = (m.field_label or "") + (f" ({m.qualifier})" if m.qualifier else "")
            payload = {
                "field_key": m.field_key,
                "candidates": [{"key": c.key, "label": c.label, "score": c.score} for c in m.candidates],
                "method": m.method,
                "score": m.score,
            }
            common = {
                "file_id": loaded.record.id,
                "file_name": loaded.record.original_name,
                "sheet": sheet.name,
                "column": m.column,
                "before": m.column,
                "payload": payload,
            }
            if m.status in ("auto", "confirmed"):
                after = label if m.field_key else "(해당 없음)"
                decision = "approved" if m.status == "confirmed" else None
                items.append(_item("auto", "mapping", reason, source=source, after=after, decision=decision, **common))
            else:
                after = label if m.field_key else "(매핑 없음)"
                items.append(_item("pending", "mapping", reason, source=source, after=after, **common))
    return items


def _correction_items(loaded: _Loaded) -> list[ReviewItem]:
    items = []
    for sheet in loaded.parsed.sheets:
        items += _header_items(loaded, sheet)
        for c in sheet.corrections:
            column = sheet.columns[c["column"]]
            where = _rows(c["rows"], c["count"])
            common = {
                "file_id": loaded.record.id,
                "file_name": loaded.record.original_name,
                "sheet": sheet.name,
                "column": column,
                "row": c["rows"][0],
                "payload": {"count": c["count"], "rows": c["rows"]},
            }
            if c["kind"] == "number":
                reason = (
                    f"'{column}' 열의 숫자 문자열 {c['count']}칸({where})을 숫자로 바꿉니다. "
                    "천 단위 쉼표·공백을 지웁니다."
                )
                before, after = f'"{c["before"]}"', _fmt(c["after"])
            else:
                reason = f"'{column}' 열의 병합 셀 {c['count']}칸({where})을 위쪽 값 '{c['after']}'(으)로 채웁니다."
                before, after = "(빈 칸)", str(c["after"])
            label = f" 외 {c['count'] - 1}칸" if c["count"] > 1 else ""
            items.append(_item("auto", c["kind"], reason, before=before, after=after + label, **common))
    return items


def _header_items(loaded: _Loaded, sheet: SheetResult) -> list[ReviewItem]:
    if len(sheet.header_cells) < 2:
        return []
    items = []
    for j, name in enumerate(sheet.columns):
        parts = [str(row[j]) for row in sheet.header_cells if j < len(row) and row[j] is not None]
        if len(dict.fromkeys(parts)) < 2:
            continue
        items.append(
            _item(
                "auto",
                "header",
                f"2단 헤더의 위 칸 '{parts[0]}'과(와) 아래 칸 '{parts[-1]}'을(를) 합쳐 "
                f"열 이름 '{name}'을(를) 만듭니다.",
                file_id=loaded.record.id,
                file_name=loaded.record.original_name,
                sheet=sheet.name,
                column=name,
                row=sheet.header_row,
                before=" / ".join(dict.fromkeys(parts)),
                after=name,
            )
        )
    return items


def _entity_items(
    result: ValidationResult, files: list[FileInput], resolver: EntityResolver, labels: dict[str, str]
) -> list[ReviewItem]:
    """labels: 표준 항목 key → 한국어 이름 (위치 표시에 내부 key를 노출하지 않음)"""
    items = []
    for e in result.entities:
        first = e.candidates[0] if e.candidates else None
        items.append(
            _item(
                "pending",
                "entity",
                entity_reason(e.status, e.value, e.note, e.candidates),
                file_name=", ".join(e.files),
                column=labels.get(e.field, e.field),
                before=e.value,
                after=f"{first['name']} ({first['code']})" if first else "(후보 없음)",
                payload={"status": e.status, "candidates": e.candidates, "field": e.field, "count": e.count},
            )
        )
    # 용어사전으로 이름이 정규화되는 값 (별칭·약칭) — 자동 적용
    seen: dict[tuple[str, str], ReviewItem] = {}
    for f in files:
        for s in f.sheets:
            for row in s.sheet.body:
                if row["kind"] != "data":
                    continue
                for field_key, name in row_entity_names(s, row["cells"]):
                    if field_key == "region_code":
                        continue
                    res = resolver.resolve(name, field_key)
                    if res is None or res.status != "resolved" or res.note or res.ref is None:
                        continue
                    if normalize_name(name) == normalize_name(res.ref.name):
                        continue
                    key = (field_key, name)
                    if key in seen:
                        payload = json.loads(seen[key].payload)
                        payload["count"] += 1
                        seen[key].payload = json.dumps(payload, ensure_ascii=False)
                        continue
                    reason = (
                        f"용어사전 별칭 '{name}'에 따라 '{res.ref.name}'(으)로 바꿉니다."
                        if res.via == "alias"
                        else f"'{name}'은(는) 지역 약칭이라 정식명칭 '{res.ref.name}'(으)로 바꿉니다."
                    )
                    seen[key] = _item(
                        "auto",
                        "entity",
                        reason,
                        file_id=f.file_id,
                        file_name=f.file_name,
                        sheet=s.sheet.name,
                        row=row["row"],
                        column=labels.get(field_key, field_key),
                        before=name,
                        after=f"{res.ref.name} ({res.ref.code})",
                        payload={"code": res.ref.code, "count": 1, "via": res.via},
                    )
    return items + list(seen.values())


# ---------------------------------------------------------------- 상태·점수


@dataclass
class Summary:
    score: int
    summary: str
    blocks: int
    pending: int
    pending_open: int
    auto: int
    warnings: int
    can_complete: bool


def summarize(session: ReviewSession, items: list[ReviewItem]) -> Summary:
    excluded = set(json.loads(session.excluded_file_ids))
    active = [i for i in items if i.file_id is None or i.file_id not in excluded or i.kind == "pii"]
    blocks = sum(1 for i in active if i.category == "block")
    pending = [i for i in active if i.category == "pending"]
    open_ = sum(1 for i in pending if i.decision is None)
    auto = sum(1 for i in active if i.category == "auto")
    warnings = sum(1 for i in active if i.category == "warning")
    score = (
        100 - BLOCK_PENALTY * blocks - PENDING_PENALTY * open_ - min(WARNING_PENALTY_MAX, WARNING_PENALTY * warnings)
    )
    if blocks:
        score = min(score, BLOCKED_SCORE_CAP)
    score = max(0, score)
    if session.status == "ready":
        text = f"검토 완료 — 통합 준비가 끝났습니다 (자동 보정 {auto}건 적용 예정)."
    elif blocks:
        text = f"통합 불가 — 차단 {blocks}건을 원본에서 고친 뒤 다시 검사하세요."
    elif open_:
        text = f"확인 필요 {open_}건이 남았습니다 — 승인 또는 거부하면 통합할 수 있습니다."
    else:
        text = f"통합 가능 — 자동 보정 {auto}건이 적용됩니다" + (f" (경고 {warnings}건 확인)" if warnings else "") + "."
    return Summary(score, text, blocks, len(pending), open_, auto, warnings, blocks == 0 and open_ == 0)


def session_items(db: Session, session: ReviewSession) -> list[ReviewItem]:
    items = list(db.scalars(select(ReviewItem).where(ReviewItem.session_id == session.id)))
    return sorted(items, key=lambda i: (ORDER.get(i.category, 9), i.id))


# ---------------------------------------------------------------- 승인·거부


def decide(db: Session, session: ReviewSession, item: ReviewItem, decision: str, choice: str | None) -> None:
    if session.status != "in_review":
        raise ReviewError("검토가 끝난 세션은 바꿀 수 없습니다. 다시 검사해 새 검토를 시작하세요.")
    if item.category != "pending":
        raise ReviewError("확인 필요 항목만 승인·거부할 수 있습니다.")
    payload = json.loads(item.payload)
    now = datetime.now(UTC)
    if item.kind == "mapping":
        _decide_mapping(db, item, payload, decision, choice)
    elif item.kind == "entity":
        codes = [c["code"] for c in payload.get("candidates", [])]
        if decision == "approved":
            picked = choice or (codes[0] if codes else None)
            if picked is None or picked not in codes:
                raise ReviewError("후보 중에서 연결할 항목을 골라야 승인할 수 있습니다.")
            item.choice = picked
            name = next(c["name"] for c in payload["candidates"] if c["code"] == picked)
            item.after = f"{name} ({picked})"
        else:
            item.choice = None
    elif item.kind == "pii":
        _decide_pii(db, session, item, decision, now)
    else:
        raise ReviewError(f"처리할 수 없는 항목입니다: {item.kind}")
    item.decision = decision
    item.decided_at = now
    db.commit()


def _decide_mapping(db: Session, item: ReviewItem, payload: dict[str, Any], decision: str, choice: str | None) -> None:
    engine = get_engine()
    labels = {f.key: f.label for f in engine.fields}
    if decision == "approved":
        key = choice or payload.get("field_key")
        if key not in labels:
            raise ReviewError("연결할 표준 항목을 골라야 승인할 수 있습니다.")
    else:
        key = None  # 거부 = 이 열은 매핑하지 않음('해당 없음')으로 확정
    assert item.file_id is not None and item.sheet is not None and item.column is not None
    save_decisions(db, engine, item.file_id, [DecisionInput(item.sheet, item.column, key)], learn=False)
    item.choice = key
    item.after = labels[key] if key else "(해당 없음)"


def _decide_pii(db: Session, session: ReviewSession, item: ReviewItem, decision: str, now: datetime) -> None:
    record = db.get(SourceFile, item.file_id)
    if record is None:
        raise ReviewError("파일을 찾을 수 없습니다.")
    excluded = set(json.loads(session.excluded_file_ids))
    if decision == "approved":
        record.pii_acknowledged_at = record.pii_acknowledged_at or now
        excluded.discard(record.id)
        item.after = "포함 확인 후 통합"
    else:
        record.pii_acknowledged_at = None
        excluded.add(record.id)
        item.after = "이번 통합에서 제외"
    session.excluded_file_ids = json.dumps(sorted(excluded))


def complete(db: Session, session: ReviewSession) -> dict[str, int]:
    """검토 완료. 차단·대기 항목이 없어야 한다. '이 형식 기억하기'면 승인한 열 이름·기관명을 학습한다."""
    if session.status != "in_review":
        raise ReviewError("이미 검토가 끝난 세션입니다.")
    items = session_items(db, session)
    summary = summarize(session, items)
    if not summary.can_complete:
        raise ReviewError(summary.summary)
    learned = {"columns": 0, "names": 0}
    if session.remember_format:
        engine = get_engine()
        for item in items:
            if item.category != "pending" or item.decision != "approved" or not item.choice:
                continue
            if item.kind == "mapping" and item.file_id is not None and item.column is not None:
                if learn_column(db, engine, item.file_id, item.column, item.choice) is None:
                    learned["columns"] += 1
            elif item.kind == "entity" and item.before:
                normalized = normalize_name(item.before)
                exists = db.scalar(
                    select(GlossaryAlias.id).where(
                        GlossaryAlias.normalized == normalized, GlossaryAlias.target_code == item.choice
                    )
                )
                if exists is None:
                    db.add(
                        GlossaryAlias(
                            alias=item.before, normalized=normalized, target_code=item.choice, source="learned"
                        )
                    )
                    db.flush()
                    learned["names"] += 1
    session.status = "ready"
    session.completed_at = datetime.now(UTC)
    db.commit()
    return learned
