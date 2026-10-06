"""재사용(자동화): 완료된 실행의 승인된 규칙을 저장하고, 새 파일에 같은 규칙을 바로 적용해 실행한다.

저장하는 것: 열 매핑 규칙(열 이름 → 표준 항목), 이름 연결 규칙(기관·지역 이름 → 7자리 코드),
검증 조건(필수 기관, 기관 열, 증감 기준, 기준 기간), 출력 형식(결과 열 순서). 입력 파일은 저장하지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.integration import KST, IntegrationError, integrate
from app.mapping import get_engine
from app.models import Automation, IntegrationRun, ReviewSession, SourceFile
from app.review import column_rule_key, complete, create_session, session_items, summarize

logger = logging.getLogger(__name__)

VALIDATION_KEYS = ("required_entities", "entity_field", "change_threshold", "expected_period")


class AutomationError(Exception):
    pass


def rules_from_session(db: Session, session: ReviewSession) -> dict[str, Any]:
    """검토가 끝난 세션에서 승인·적용된 규칙을 모은다 (제외한 파일의 항목은 빼고, 같은 열 이름은 처음 것을 쓴다)."""
    labels = {f.key: f.label for f in get_engine().fields}
    excluded = set(json.loads(session.excluded_file_ids))
    columns: dict[str, dict[str, Any]] = {}
    entities: dict[str, str | None] = {}
    for item in session_items(db, session):
        if item.file_id is not None and item.file_id in excluded:
            continue
        if item.kind == "mapping" and item.column is not None:
            if item.category == "auto":
                key = json.loads(item.payload).get("field_key")
            elif item.category == "pending" and item.decision is not None:
                key = item.choice if item.decision == "approved" else None
            else:
                continue
            columns.setdefault(
                column_rule_key(item.column),
                {
                    "key": column_rule_key(item.column),
                    "column": item.column,
                    "field_key": key,
                    "field_label": labels.get(key) if key else None,
                },
            )
        elif item.kind == "entity" and item.before and item.decision is not None:
            # 확인 필요였던 이름(또는 이전 자동화에서 이어받은 이름)의 승인 → 코드, 거부 → 그대로 둠(None)
            if "candidates" in json.loads(item.payload):
                entities.setdefault(item.before, item.choice if item.decision == "approved" else None)
    return {"columns": list(columns.values()), "entities": entities}


def save_from_run(db: Session, run: IntegrationRun, name: str) -> Automation:
    name = name.strip()
    if not name:
        raise AutomationError("자동화 이름을 입력하세요.")
    if len(name) > 100:
        raise AutomationError("자동화 이름은 100자 이하로 입력하세요.")
    if run.status != "done":
        raise AutomationError("완료된 실행만 자동화로 저장할 수 있습니다.")
    session = db.get(ReviewSession, run.review_session_id) if run.review_session_id is not None else None
    if session is None:
        raise AutomationError("이 실행의 검토 기록이 없어 규칙을 저장할 수 없습니다.")
    if db.scalar(select(Automation.id).where(Automation.name == name)) is not None:
        raise AutomationError(f"같은 이름의 자동화가 이미 있습니다: {name}")
    rules = rules_from_session(db, session)
    params = json.loads(session.params)
    summary = json.loads(run.summary or "{}")
    automation = Automation(
        name=name,
        source_run_id=run.id,
        column_rules=json.dumps(rules["columns"], ensure_ascii=False),
        entity_rules=json.dumps(rules["entities"], ensure_ascii=False),
        validation=json.dumps({k: params.get(k) for k in VALIDATION_KEYS}, ensure_ascii=False),
        output_format=json.dumps({"columns": summary.get("columns", [])}, ensure_ascii=False),
        updated_at=datetime.now(KST),
    )
    db.add(automation)
    db.commit()
    logger.info(
        "automation %s saved from run %s: %d column rules, %d entity rules",
        automation.id,
        run.id,
        len(rules["columns"]),
        len(rules["entities"]),
    )
    return automation


def run_automation(
    db: Session,
    automation: Automation,
    file_ids: list[int],
    expected_period: str | None,
    baseline_file_ids: list[int] | None = None,
    use_llm: bool = True,
) -> tuple[IntegrationRun, ReviewSession]:
    """새 파일에 자동화 규칙을 적용한다. 확인할 항목이 없으면 바로 통합 결과까지 만들고(완료),
    새 열 등 확인할 항목이 남으면 실행 기록을 '검토'로 남긴다 (검토 패널에서 이어서 처리)."""
    file_ids = list(dict.fromkeys(file_ids))
    baseline = list(dict.fromkeys(baseline_file_ids or []))
    if not file_ids:
        raise AutomationError("실행할 파일을 하나 이상 고르세요.")
    if set(file_ids) & set(baseline):
        raise AutomationError("같은 파일을 실행 대상과 전월 자료로 함께 쓸 수 없습니다.")
    missing = [fid for fid in file_ids + baseline if db.get(SourceFile, fid) is None]
    if missing:
        raise AutomationError(f"파일을 찾을 수 없습니다: {missing}")
    started = datetime.now(KST)
    validation = json.loads(automation.validation)
    params = {
        "file_ids": file_ids,
        "baseline_file_ids": baseline,
        "expected_period": expected_period or None,
        "required_entities": validation.get("required_entities") or [],
        "entity_field": validation.get("entity_field"),
        "change_threshold": validation.get("change_threshold") or 0.5,
    }
    session = create_session(db, params, use_llm=use_llm, automation=automation)
    run = IntegrationRun(
        review_session_id=session.id,
        automation_id=automation.id,
        trigger="automation",
        status="review",
        input_file_count=len(file_ids),
        started_at=started,
        file_ids=json.dumps(file_ids),
        summary="{}",
    )
    db.add(run)
    db.commit()
    if not summarize(session, session_items(db, session)).can_complete:
        return run, session
    complete(db, session)
    try:
        run = integrate(db, session)
    except IntegrationError as exc:
        db.rollback()
        failed = db.get(IntegrationRun, run.id)
        if failed is not None and failed.status != "failed":
            failed.status = "failed"
            failed.error = str(exc)[:1000]
            failed.finished_at = datetime.now(KST)
            db.commit()
        if failed is not None:
            run = failed
    return run, session


def delete_automation(db: Session, automation: Automation) -> None:
    """자동화만 지운다. 실행 기록과 검토 기록은 남기고 연결만 끊는다."""
    db.execute(update(IntegrationRun).where(IntegrationRun.automation_id == automation.id).values(automation_id=None))
    db.execute(update(ReviewSession).where(ReviewSession.automation_id == automation.id).values(automation_id=None))
    db.delete(automation)
    db.commit()
