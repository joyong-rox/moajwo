"""공통 key 연결의 '저장한 작업'(설정 재사용): 한 번 확인한 연결 설정을 파일 번호가 아니라 **입력 역할·시트·열
이름(위치)** 기준으로 저장해 두고, 다음 자료(새로 올린 파일)에 같은 규칙을 맞춰 요청(app/keyjoin.py KeyJoinIn)을
다시 만든다.

- 저장하는 것: 작업 이름·소유자·설정 버전, 입력마다 역할 번호·저장 때의 파일 이름·시트 이름·순번, key 열(이름·열
  문자·위치, 복합 key 순서), 가져올 열(이름·열 문자·위치·결과 이름·순서), 기준 입력, 범위, key 결과 이름,
  (다음 단계) key별 집계 설정, 저장 때 key 열의 자료형 분포(글자·정수 수 — 새 파일에서 바뀌었는지 알리는 데만).
- 저장하지 않는 것: 파일 자체·파일 번호(새 파일에 다시 맞춘다), 개인정보 포함 확인, 추정 행의 포함/제외 결정,
  요청 번호, 확인 결과(basis). 새 파일은 연결 확인(plan)을 다시 거쳐야 만들 수 있다 — 기존 규칙 그대로(행 결정·
  개인정보 확인도 새 파일 기준).
- 새 파일 맞추기(match): 파일마다 시트·열 구조(key 열·가져올 열이 같은 이름으로 있는지, 시트 이름·순번, 파일
  이름)로 점수를 매겨 역할에 배정한다. **파일 이름만으로 맞추지 않는다**(key 열이 같은 이름으로 없는 시트는 후보가
  아님). 확실하지 않으면(같은 점수의 파일이 둘, 같은 이름의 열이 여럿, 열이 없음) 자동으로 정하지 않고 후보를 보여
  사용자가 고르게 한다(apply의 assignments·columns). 이름이 같고 위치만 바뀐 열은 그대로 맞추고 '열 위치 변경'으로
  알린다. 이름이 다른 열은 위치가 같아도 자동으로 쓰지 않는다(사용자가 고른 뒤에만).
- 갱신(PUT)은 화면이 본 버전(expected_version)을 **반드시** 받는다(재발급의 expected_auth_version과 같은 계약 — 없거나
  정수가 아니면 422). 번호·소유자·버전을 조건으로 한 UPDATE 한 문장으로만 바꾸고, 그 사이 다른 창의 갱신이 먼저
  반영됐으면 덮지 않고 409(KEYJOIN_JOB_VERSION_CHANGED). 화면은 자동으로 다시 보내지 않고 최신 목록을 다시 읽는다.
  버전은 지금처럼 설정을 바꿀 때만 +1이다(이름만 바꾸는 갱신은 버전 그대로 — 조건에는 쓴다).
- 소유자만 목록·조회·수정·삭제·맞추기를 한다(다른 사용자는 최고 관리자라도 404). 표준 통합 자동화
  (app/automation.py)와 그 화면·표·API는 그대로다.
- key별 집계 후 연결(aggregate) 설정도 입력마다 저장하고(함수·열 이름·위치·결과 이름), 새 파일에서는 집계 열을 가져올
  열과 같은 규칙으로 맞춘다 (행 수는 열이 없어 맞출 것이 없다).
- 응답에는 가린 이름(mask_text)만, 문구에는 위치·개수·코드만 넣는다. key 원문·셀 값은 어디에도 없다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep
from app.bundle import FileInfo, SheetInfo, file_info
from app.db import get_session
from app.files import get_file_for
from app.integration import KST
from app.keyjoin import (
    MAX_INPUTS,
    SCOPE_LABEL,
    AggItemIn,
    AggregateIn,
    BaseRef,
    ColumnIn,
    InputIn,
    KeyJoinIn,
    Plan,
    _Place,
    build_plan,
    check_request,
)
from app.models import KeyJoinJob, SourceFile
from app.name_limits import StoredText, cell_place
from app.output_slot import output_slot
from app.pii import mask_text
from app.timefmt import stored_kst, utc_to_kst

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/keyjoin/jobs", tags=["keyjoin"])

SPEC_FORMAT = 1  # spec JSON 형식 (필드가 바뀌면 올린다 — 이전 형식은 읽을 때 기본값으로 채운다)
NAME_MAX = 100  # keyjoin_jobs.name
MAX_MATCH_FILES = 50  # 맞추기에 보내는 파일 수 한도
NOT_FOUND = "저장한 작업을 찾을 수 없습니다."
# 맞추기 점수 (높을수록 먼저 배정): key 열은 모두 같은 이름으로 있어야 후보다 — 파일 이름·시트 이름은 보조 근거일 뿐이다
SCORE_KEY, SCORE_PICK, SCORE_FILE_NAME, SCORE_SHEET_NAME, SCORE_TAB = 10, 2, 3, 2, 1
KIND_TEXT = {"key": "key", "pick": "가져올 열", "agg": "집계 항목"}
KIND_TAG = {"key": "k", "pick": "p", "agg": "a"}  # apply의 columns 선택 태그 접두어


# ---------------------------------------------------------------- 요청·응답 형식


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StoredText = Field(min_length=1, max_length=NAME_MAX)
    request: KeyJoinIn  # 지금 화면의 연결 설정 (파일 번호·시트·열 ID) — 저장 때 역할·이름·위치로 바꿔 둔다


class UpdateIn(BaseModel):
    """갱신: expected_version은 화면이 본 작업 버전 — **필수**(1 이상의 정수). 지금 버전과 다르면(다른 창의 갱신이
    먼저 반영됨) 덮지 않고 409. 본문·버전이 없거나 정수가 아니면(불리언·문자열·실수 포함) 422 — 조건 없는 갱신
    경로는 없다."""

    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=1)
    name: StoredText | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    request: KeyJoinIn | None = None  # 있으면 설정을 통째로 바꾼다 (버전 +1)


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ids: list[Annotated[int, Field(ge=1, le=2**63 - 1)]] = Field(min_length=1, max_length=MAX_MATCH_FILES)


class AssignmentIn(BaseModel):
    """역할 하나에 쓸 파일·시트. columns: 자동으로 정하지 못한 열의 선택 — 'k{n}'(key n번째)·'p{n}'(가져올 열 n번째) →
    column_id. 가져올 열은 null이면 이번 작업에서 뺀다 (key 열은 뺄 수 없다)."""

    model_config = ConfigDict(extra="forbid")

    role: int = Field(ge=1, le=MAX_INPUTS)
    file_id: int = Field(ge=1, le=2**63 - 1)
    sheet_id: str = Field(pattern=r"^s\d{1,3}$")
    columns: dict[
        Annotated[str, Field(pattern=r"^[kpa]\d{1,3}$")], Annotated[str, Field(pattern=r"^c\d{1,4}$")] | None
    ] = Field(default_factory=dict, max_length=60)


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[AssignmentIn] = Field(min_length=1, max_length=MAX_INPUTS)


def _conflict(code: str, message: str, problems: list[str] | None = None) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {"message": message, "problems": problems or [], "code": code})


def _bad(text: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, text)


# ---------------------------------------------------------------- 저장 형식 (spec)


def spec_from_plan(plan: Plan) -> dict[str, Any]:
    """연결 확인이 끝난(만들 수 있는) 구성에서 저장할 규칙을 뽑는다 — 파일 번호 대신 역할·이름·위치."""
    inputs = []
    for ip in plan.inputs:
        s, ix = ip.sheet, ip.ix
        assert s is not None
        keys = [{"name": s.column_names[j], "letter": ip.letter(j), "index": j} for j in ip.key_ids]
        cols = (
            []  # 집계 입력은 가져올 열이 없다 (pick_ids는 집계 항목이 읽는 열)
            if ip.aggregated
            else [
                {"name": s.column_names[j], "letter": ip.letter(j), "index": j, "out_name": ip.req.columns[p].out_name}
                for p, j in enumerate(ip.pick_ids)
            ]
        )
        kinds = [{"text": k["text"], "int": k["int"]} for k in ix.kinds] if ix is not None else None
        aggs = (
            [
                {
                    "func": func,
                    "name": s.column_names[j] if j is not None else None,
                    "letter": ip.letter(j) if j is not None else None,
                    "index": j,
                    "out_name": a.out_name,
                }
                for a, (func, j) in zip(ip.req.aggregate.items, ip.agg_items, strict=True)
            ]
            if ip.req.aggregate is not None
            else None
        )
        inputs.append(
            {
                "role": ip.index + 1,
                "file_name": ip.info.name,
                "sheet": {"name": s.name, "tab": s.tab, "index": ip.sheet_index},
                "key_columns": keys,
                "columns": cols,
                "aggregate": aggs,  # key별 집계 후 연결 설정 (없으면 None)
                "is_base": ip is plan.base,
                "key_kinds": kinds,
            }
        )
    return {
        "format": SPEC_FORMAT,
        "inputs": inputs,
        "scope": plan.req.scope,
        "key_names": plan.req.key_names,
        "aggregate": None,  # 다음 단계(key별 집계 후 연결)에서 쓴다 — 지금은 항상 None
    }


def _spec_for(db: Session, user: CurrentUser, req: KeyJoinIn) -> dict[str, Any]:
    """저장할 설정을 지금 파일로 한 번 확인한다(plan — 읽기 전용). 만들 수 없는 구성(차단)은 저장하지 않는다."""
    with output_slot():
        plan = build_plan(db, user, req)
        db.rollback()
    if plan.blocks:
        raise _conflict(
            "KEYJOIN_JOB_NOT_READY",
            "지금 설정으로는 만들 수 없어 저장하지 않았습니다 — 연결 확인의 문제를 먼저 해결하세요.",
            [p.text for p in plan.blocks],
        )
    return spec_from_plan(plan)


def _load_spec(job: KeyJoinJob) -> dict[str, Any]:
    spec = json.loads(job.spec)
    spec.setdefault("aggregate", None)
    spec.setdefault("key_names", None)
    for ip in spec["inputs"]:
        ip.setdefault("key_kinds", None)
        ip.setdefault("aggregate", None)
    return spec


def job_out(job: KeyJoinJob) -> dict[str, Any]:
    """목록·조회 응답 — 이름은 가리고, 파일 번호는 없다(저장하지 않음)."""
    spec = _load_spec(job)
    inputs = []
    for ip in spec["inputs"]:
        inputs.append(
            {
                "role": ip["role"],
                "file_name": mask_text(ip["file_name"]),
                "sheet": {"name": mask_text(ip["sheet"]["name"]), "tab": ip["sheet"]["tab"]},
                "key_columns": [{"name": mask_text(c["name"]), "letter": c["letter"]} for c in ip["key_columns"]],
                "columns": [
                    {
                        "name": mask_text(c["name"]),
                        "letter": c["letter"],
                        "out_name": mask_text(c["out_name"]) if c["out_name"] is not None else None,
                    }
                    for c in ip["columns"]
                ],
                "aggregate": [
                    {
                        "func": a["func"],
                        "name": mask_text(a["name"]) if a.get("name") is not None else None,
                        "letter": a.get("letter"),
                        "out_name": mask_text(a["out_name"]) if a.get("out_name") is not None else None,
                    }
                    for a in ip["aggregate"]
                ]
                if ip.get("aggregate")
                else None,
                "is_base": ip["is_base"],
                "key_kinds": ip.get("key_kinds"),
            }
        )
    key_names = spec["key_names"]
    return {
        "id": job.id,
        "name": job.name,
        "owner_id": job.owner_id,
        "version": job.version,
        "scope": spec["scope"],
        "scope_label": SCOPE_LABEL[spec["scope"]],
        "input_count": len(inputs),
        "key_columns": len(spec["inputs"][0]["key_columns"]) if spec["inputs"] else 0,
        "inputs": inputs,
        "key_names": [mask_text(n) for n in key_names] if key_names else None,
        "aggregate": spec["aggregate"],
        "created_at": utc_to_kst(job.created_at),
        "updated_at": stored_kst(job.updated_at),
    }


# ---------------------------------------------------------------- 새 파일에 맞추기


def _place(sheet: SheetInfo) -> _Place:
    return _Place(sheet.name, sheet.tab, sheet.column_names, sheet.source_columns)


def _letter(sheet: SheetInfo, j: int) -> str:
    return cell_place(_place(sheet), None, j).removesuffix("열")


def resolve_column(sheet: SheetInfo, saved: dict[str, Any], kind: str, pos: int) -> dict[str, Any]:
    """저장한 열 하나를 새 시트의 열에 맞춘다 — 이름이 하나뿐이면 그 열(위치가 바뀌었으면 알림), 같은 이름이 여럿이면
    사용자가 고르게(ambiguous), 없으면 고르거나 빼게(missing — 같은 위치의 다른 이름 열은 후보로만 보인다)."""
    names = sheet.column_names
    hits = [j for j, n in enumerate(names) if n == saved["name"]]

    def cands(js: list[int]) -> list[dict[str, Any]]:
        return [{"column_id": f"c{j}", "name": mask_text(names[j]), "letter": _letter(sheet, j)} for j in js]

    base: dict[str, Any] = {
        "kind": kind,
        "position": pos,
        "saved": {"name": mask_text(saved["name"]), "letter": saved["letter"]},
        "out_name": mask_text(saved["out_name"]) if saved.get("out_name") is not None else None,
    }
    if len(hits) == 1:
        j = hits[0]
        if j == saved["index"]:
            return {**base, "column_id": f"c{j}", "status": "same", "candidates": cands([j]), "note": None}
        note = f"열 위치 변경: {saved['letter']}열 → {_letter(sheet, j)}열 (이름 같음)"
        return {**base, "column_id": f"c{j}", "status": "moved", "candidates": cands([j]), "note": note}
    if len(hits) > 1:
        note = f"같은 이름의 열이 {len(hits)}개 있어 자동으로 정하지 않았습니다 — 쓸 열을 고르세요"
        return {**base, "column_id": None, "status": "ambiguous", "candidates": cands(hits), "note": note}
    pos_cands = [saved["index"]] if saved["index"] < len(names) else []
    what = "쓸 열을 고르세요" if kind == "key" else "쓸 열을 고르거나 이번 작업에서 빼세요"
    note = f"저장 때의 열 이름이 이 시트에 없습니다 — {what} (같은 위치의 열은 이름이 달라 자동으로 쓰지 않음)"
    return {**base, "column_id": None, "status": "missing", "candidates": cands(pos_cands), "note": note}


def resolve_input(sheet: SheetInfo, saved: dict[str, Any]) -> list[dict[str, Any]]:
    out = [resolve_column(sheet, c, "key", n) for n, c in enumerate(saved["key_columns"])]
    out += [resolve_column(sheet, c, "pick", n) for n, c in enumerate(saved["columns"])]
    # 집계 항목의 열 (행 수는 열이 없어 맞출 것이 없다) — 열 선택 태그는 'a{n}'
    aggs = saved.get("aggregate") or []
    out += [resolve_column(sheet, a, "agg", n) for n, a in enumerate(aggs) if a.get("index") is not None]
    return out


@dataclass
class _Cand:
    file_id: int
    sheet_index: int
    score: int
    columns: list[dict[str, Any]]


def _score(saved: dict[str, Any], info: FileInfo, sheet: SheetInfo, columns: list[dict[str, Any]]) -> int | None:
    """후보 점수 — key 열이 모두(같은 이름으로) 있어야 후보다. 파일 이름·시트 이름은 보조 근거."""
    keys = [c for c in columns if c["kind"] == "key"]
    if any(c["column_id"] is None for c in keys):
        return None
    picks = [c for c in columns if c["kind"] in ("pick", "agg") and c["column_id"] is not None]
    score = SCORE_KEY * len(keys) + SCORE_PICK * len(picks)
    if info.name == saved["file_name"]:
        score += SCORE_FILE_NAME
    if sheet.name == saved["sheet"]["name"]:
        score += SCORE_SHEET_NAME
    if sheet.tab == saved["sheet"]["tab"]:
        score += SCORE_TAB
    return score


def _candidates(saved: dict[str, Any], infos: dict[int, FileInfo]) -> list[_Cand]:
    """파일마다 가장 잘 맞는 시트 하나 — 점수 내림차순, 같은 점수는 파일 번호 순."""
    out: list[_Cand] = []
    for fid, info in infos.items():
        best: _Cand | None = None
        for si, sheet in enumerate(info.sheets):
            columns = resolve_input(sheet, saved)
            score = _score(saved, info, sheet, columns)
            if score is None:
                continue
            if best is None or score > best.score:
                best = _Cand(fid, si, score, columns)
        if best is not None:
            out.append(best)
    return sorted(out, key=lambda c: (-c.score, c.file_id))


def propose(spec: dict[str, Any], infos: dict[int, FileInfo]) -> dict[int, _Cand | list[_Cand]]:
    """역할마다 파일을 배정한다. 확실한 것(후보 중 점수가 가장 높은 파일이 하나뿐)부터 정하고, 남은 역할에 남은
    파일 후보가 하나뿐이면 그것으로, 둘 이상이거나 없으면 사용자가 고르게 후보 목록을 돌려준다."""
    cands = {ip["role"]: _candidates(ip, infos) for ip in spec["inputs"]}
    assigned: dict[int, _Cand] = {}
    used: set[int] = set()
    changed = True
    while changed:
        changed = False
        pending = [r for r in cands if r not in assigned]
        picks: list[tuple[int, int, _Cand]] = []
        for role in pending:
            free = [c for c in cands[role] if c.file_id not in used]
            if not free:
                continue
            top = [c for c in free if c.score == free[0].score]
            if len(top) == 1:
                picks.append((free[0].score, role, free[0]))
        for _, role, cand in sorted(picks, key=lambda x: (-x[0], x[1])):
            if cand.file_id in used:
                continue  # 더 높은 점수의 다른 역할이 먼저 가져갔다 — 다음 바퀴에 다시 본다
            assigned[role] = cand
            used.add(cand.file_id)
            changed = True
    result: dict[int, _Cand | list[_Cand]] = {}
    for role in cands:
        if role in assigned:
            result[role] = assigned[role]
            continue
        free = [c for c in cands[role] if c.file_id not in used]
        if len(free) == 1:
            result[role] = free[0]
            used.add(free[0].file_id)
        else:
            result[role] = free  # 0개(맞는 파일 없음) 또는 2개 이상(같은 점수 — 고르게)
    return result


def _own_infos(db: Session, user: CurrentUser, file_ids: list[int]) -> dict[int, FileInfo]:
    """맞출 파일들의 시트·열 정보 — 본인 파일만 (남의 파일·없는 파일은 없는 것처럼 422)."""
    infos: dict[int, FileInfo] = {}
    hidden: list[int] = []
    for fid in dict.fromkeys(file_ids):
        record: SourceFile | None = db.get(SourceFile, fid)
        own = record is not None and (record.uploaded_by == user.id or (record.uploaded_by is None and user.is_manager))
        if not own:
            hidden.append(fid)
            continue
        infos[fid] = file_info(db, get_file_for(db, user, fid))
    if hidden:
        raise _bad(f"파일을 찾을 수 없습니다: {hidden}")
    return infos


def _role_out(
    spec_input: dict[str, Any], cand: _Cand | None, cands: list[_Cand], infos: dict[int, FileInfo], status_: str
) -> dict[str, Any]:
    def cand_out(c: _Cand) -> dict[str, Any]:
        info = infos[c.file_id]
        sheet = info.sheets[c.sheet_index]
        return {
            "file_id": c.file_id,
            "sheet_id": f"s{c.sheet_index}",
            "file_name": mask_text(info.name),
            "sheet_name": mask_text(sheet.name),
            "tab": sheet.tab,
            "score": c.score,
        }

    notes: list[str] = []
    if cand is not None:
        info = infos[cand.file_id]
        sheet = info.sheets[cand.sheet_index]
        role = spec_input["role"]
        if info.name != spec_input["file_name"]:
            notes.append(
                f"입력 {role}: 파일 이름이 저장 때와 다릅니다 (저장 '{mask_text(spec_input['file_name'])}' → "
                f"이번 #{cand.file_id} '{mask_text(info.name)}') — 열 구조로 맞췄습니다."
            )
        if sheet.name != spec_input["sheet"]["name"] or sheet.tab != spec_input["sheet"]["tab"]:
            notes.append(
                f"입력 {role}: 시트가 저장 때와 다릅니다 (저장 '{mask_text(spec_input['sheet']['name'])}' "
                f"{spec_input['sheet']['tab'] + 1}번째 → 이번 '{mask_text(sheet.name)}' {sheet.tab + 1}번째)."
            )
        for c in cand.columns:
            if c["note"]:
                what = KIND_TEXT[c["kind"]] + f" {c['position'] + 1}"
                notes.append(f"입력 {role} {what} ({c['saved']['name']}): {c['note']}")
    return {
        "role": spec_input["role"],
        "status": status_,
        "file_id": cand.file_id if cand is not None else None,
        "sheet_id": f"s{cand.sheet_index}" if cand is not None else None,
        "candidates": [cand_out(c) for c in ([cand] if cand is not None else cands)],
        "columns": cand.columns if cand is not None else [],
        "notes": notes,
    }


def _build_request(
    spec: dict[str, Any], roles: list[dict[str, Any]], overrides: dict[int, dict[str, str | None]]
) -> tuple[KeyJoinIn | None, list[str]]:
    """배정·열 선택으로 요청을 만든다. 정하지 못한 열이 남으면 막힌 이유를 돌려준다(요청 없음)."""
    blocked: list[str] = []
    inputs: list[InputIn] = []
    base: BaseRef | None = None
    for spec_input, role in zip(spec["inputs"], roles, strict=True):
        n = spec_input["role"]
        if role["file_id"] is None:
            blocked.append(f"입력 {n}: 맞는 파일이 없거나 여럿이라 정하지 못했습니다 — 파일을 고르세요.")
            continue
        chosen = overrides.get(n, {})
        keys: list[str] = []
        cols: list[ColumnIn] = []
        agg_cids: dict[int, str] = {}
        blocked_before = len(blocked)
        for c in role["columns"]:
            tag = f"{KIND_TAG[c['kind']]}{c['position']}"
            cid = c["column_id"]
            if tag in chosen:
                cid = chosen[tag]
                if cid is None and c["kind"] != "pick":
                    what = "key 열" if c["kind"] == "key" else "집계 항목의 열"
                    blocked.append(
                        f"입력 {n} {KIND_TEXT[c['kind']]} {c['position'] + 1}: {what}은 뺄 수 없습니다 — "
                        "쓸 열을 고르세요."
                    )
                    continue
            elif cid is None:
                what = KIND_TEXT[c["kind"]] + f" {c['position'] + 1}"
                blocked.append(f"입력 {n} {what}: {c['note']}")
                continue
            if c["kind"] == "key":
                keys.append(cid)
            elif c["kind"] == "agg":
                agg_cids[c["position"]] = cid
            elif cid is not None:
                saved = spec_input["columns"][c["position"]]
                cols.append(ColumnIn(column_id=cid, out_name=saved["out_name"]))
        if len(blocked) > blocked_before:  # 정하지 못한 열이 있는 입력은 요청을 만들지 않는다 (막힌 이유만 돌려준다)
            continue
        aggregate = None
        if spec_input.get("aggregate"):
            aggregate = AggregateIn(
                items=[
                    AggItemIn(
                        func=a["func"],
                        column_id=agg_cids.get(m) if a["index"] is not None else None,
                        out_name=a["out_name"],
                    )
                    for m, a in enumerate(spec_input["aggregate"])
                ]
            )
        inputs.append(
            InputIn(
                file_id=role["file_id"], sheet_id=role["sheet_id"], key_columns=keys, columns=cols, aggregate=aggregate
            )
        )
        if spec_input["is_base"]:
            base = BaseRef(file_id=role["file_id"], sheet_id=role["sheet_id"])
    if blocked or base is None:
        if base is None and not blocked:
            blocked.append("기준 입력을 정하지 못했습니다.")
        return None, blocked
    req = KeyJoinIn(inputs=inputs, scope=spec["scope"], base=base, key_names=spec["key_names"])
    try:
        check_request(req)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return None, [f"이 파일들로는 요청을 만들 수 없습니다: {detail}"]
    return req, []


def apply_spec(
    spec: dict[str, Any],
    infos: dict[int, FileInfo],
    assignments: dict[int, tuple[int, int]] | None,
    overrides: dict[int, dict[str, str | None]],
) -> dict[str, Any]:
    """assignments가 없으면 자동 제안(propose), 있으면 그 배정(역할 → (파일, 시트 순번))으로 열을 맞춰 요청을 만든다."""
    roles: list[dict[str, Any]] = []
    if assignments is None:
        proposal = propose(spec, infos)
        for ip in spec["inputs"]:
            got = proposal[ip["role"]]
            if isinstance(got, _Cand):
                roles.append(_role_out(ip, got, [], infos, "assigned"))
            else:
                roles.append(_role_out(ip, None, got, infos, "ambiguous" if got else "unassigned"))
    else:
        for ip in spec["inputs"]:
            fid, si = assignments[ip["role"]]
            sheet = infos[fid].sheets[si]
            cand = _Cand(fid, si, 0, resolve_input(sheet, ip))
            roles.append(_role_out(ip, cand, [], infos, "assigned"))
    used = {r["file_id"] for r in roles if r["file_id"] is not None}
    request, blocked = _build_request(spec, roles, overrides)
    notes = [n for r in roles for n in r["notes"]]
    unused = [fid for fid in infos if fid not in used]
    if unused:
        notes.append(
            f"보낸 파일 가운데 {len(unused)}개({', '.join(f'#{f}' for f in unused)})는 어느 입력에도 쓰지 않습니다."
        )
    return {
        "roles": roles,
        "unused_files": unused,
        "ready": request is not None,
        "request": request.model_dump(mode="json") if request is not None else None,
        "notes": notes,
        "blocked": blocked,
        "expected_key_kinds": {str(ip["role"]): ip.get("key_kinds") for ip in spec["inputs"]},
    }


def kind_changes(spec: dict[str, Any], plan_out: dict[str, Any]) -> list[str]:
    """저장 때 key 열의 자료형 분포(글자·정수)와 새 파일의 연결 확인 결과를 비교해 바뀐 입력을 알린다 — 입력
    순서 = 역할 순서. (화면이 같은 규칙으로 보이고, 시험은 이 함수를 직접 부른다.)"""
    notes: list[str] = []
    for ip, got in zip(spec["inputs"], plan_out.get("inputs", []), strict=False):
        saved = ip.get("key_kinds")
        if not saved:
            continue
        for pos, (was, col) in enumerate(zip(saved, got.get("key_columns", []), strict=False)):
            now = col.get("kinds")
            if not now:
                continue
            kinds_now = {k: v for k, v in now.items() if v}
            if (was["int"] == 0 and now.get("int", 0)) or (was["text"] == 0 and now.get("text", 0)):
                notes.append(
                    f"입력 {ip['role']} key {pos + 1}: key 자료형이 저장 때(글자 {was['text']}·정수 {was['int']})와 "
                    f"다릅니다 (이번 {', '.join(f'{k} {v}' for k, v in kinds_now.items())}) — 연결 확인 결과를 "
                    "확인하세요."
                )
    return notes


# ---------------------------------------------------------------- API


def _get(db: Session, job_id: int, user: CurrentUser) -> KeyJoinJob:
    job = db.get(KeyJoinJob, job_id)
    if job is None or job.owner_id != user.id:  # 남의 작업은 있는지조차 알리지 않는다 (최고 관리자도)
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return job


def _name_taken(db: Session, user: CurrentUser, name: str, except_id: int | None = None) -> bool:
    stmt = select(KeyJoinJob.id).where(KeyJoinJob.owner_id == user.id, KeyJoinJob.name == name)
    if except_id is not None:
        stmt = stmt.where(KeyJoinJob.id != except_id)
    return db.scalar(stmt) is not None


@router.get("")
def list_jobs(db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> list[dict[str, Any]]:
    """내 저장한 작업 목록 (이름 순). 다른 사용자의 작업은 보이지 않는다."""
    stmt = select(KeyJoinJob).where(KeyJoinJob.owner_id == user.id).order_by(KeyJoinJob.name, KeyJoinJob.id)
    return [job_out(j) for j in db.scalars(stmt)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(body: CreateIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> dict[str, Any]:
    """지금 설정을 저장한 작업으로 저장한다 — 연결 확인을 한 번 거쳐(파일을 만들지 않음) 만들 수 있는 구성만.
    같은 이름이 있으면 409."""
    name = body.name.strip()
    if not name:
        raise _bad("작업 이름을 적으세요.")
    if _name_taken(db, user, name):
        raise _conflict("KEYJOIN_JOB_NAME_TAKEN", "같은 이름의 저장한 작업이 이미 있습니다 — 다른 이름을 적으세요.")
    spec = _spec_for(db, user, body.request)
    job = KeyJoinJob(owner_id=user.id, name=name, version=1, spec=json.dumps(spec, ensure_ascii=False))
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:  # 같은 이름을 동시에 저장
        db.rollback()
        raise _conflict(
            "KEYJOIN_JOB_NAME_TAKEN", "같은 이름의 저장한 작업이 이미 있습니다 — 다른 이름을 적으세요."
        ) from exc
    logger.info("keyjoin job %s saved: %d inputs", job.id, len(spec["inputs"]))
    return job_out(job)


@router.get("/{job_id}")
def get_job(job_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> dict[str, Any]:
    return job_out(_get(db, job_id, user))


@router.put("/{job_id}")
def update_job(
    job_id: int, body: UpdateIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> dict[str, Any]:
    """이름·설정을 바꾼다 (설정을 바꾸면 버전 +1 — 이전 판으로 받은 확인 결과는 요청이 달라져 만들기에 쓸 수 없다).
    화면이 본 버전(expected_version, 필수)과 지금 버전이 같을 때만 바꾼다 — 결정은 번호·소유자·버전을 조건으로 한 UPDATE
    한 문장이다(앞의 버전 비교는 헛일을 줄이는 빠른 거절일 뿐). 0행이면 그 사이 지워졌거나 남의 것이면 404, 다른 갱신이
    먼저 반영됐으면 409(KEYJOIN_JOB_VERSION_CHANGED) — 어느 쪽도 아무것도 바꾸지 않는다."""
    job = _get(db, job_id, user)
    if body.name is None and body.request is None:
        raise _bad("바꿀 이름이나 설정을 보내세요.")
    if job.version != body.expected_version:
        raise _version_changed(body.expected_version, job.version)
    values: dict[str, Any] = {"updated_at": datetime.now(KST)}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise _bad("작업 이름을 적으세요.")
        if _name_taken(db, user, name, except_id=job.id):
            raise _conflict("KEYJOIN_JOB_NAME_TAKEN", "같은 이름의 저장한 작업이 이미 있습니다 — 다른 이름을 적으세요.")
        values["name"] = name
    if body.request is not None:
        values["spec"] = json.dumps(_spec_for(db, user, body.request), ensure_ascii=False)
        values["version"] = body.expected_version + 1
    stmt = (
        update(KeyJoinJob)
        .where(KeyJoinJob.id == job_id, KeyJoinJob.owner_id == user.id, KeyJoinJob.version == body.expected_version)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    try:
        changed = db.execute(stmt).rowcount
        if changed:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict(
            "KEYJOIN_JOB_NAME_TAKEN", "같은 이름의 저장한 작업이 이미 있습니다 — 다른 이름을 적으세요."
        ) from exc
    if not changed:
        db.rollback()
        now = db.scalar(select(KeyJoinJob.version).where(KeyJoinJob.id == job_id, KeyJoinJob.owner_id == user.id))
        if now is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        raise _version_changed(body.expected_version, now)
    db.refresh(job)
    return job_out(job)


def _version_changed(seen: int, now: int) -> HTTPException:
    return _conflict(
        "KEYJOIN_JOB_VERSION_CHANGED",
        f"이 작업은 그 사이 다른 곳에서 바뀌었습니다 (이 화면이 본 판 v{seen}, 지금 v{now}) — 바꾸지 않았습니다. "
        "최신 내용을 다시 불러온 뒤 다시 저장하세요.",
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> Response:
    """저장한 작업만 지운다 — 실행 기록·결과 파일·원본은 그대로. 번호와 소유자를 한 문장(DELETE … WHERE)으로 확인하며
    지운다(조회와 삭제 사이에 다른 요청이 끼지 않음). 번호는 다시 쓰지 않으므로(0024) 옛 화면이 보낸 번호는 그 뒤 새로
    저장한 작업을 가리키지 않고 404가 된다 — 남의 작업도 있는지조차 알리지 않는 404."""
    gone = db.execute(delete(KeyJoinJob).where(KeyJoinJob.id == job_id, KeyJoinJob.owner_id == user.id)).rowcount
    db.commit()
    if not gone:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/match")
def match_job(
    job_id: int, body: MatchIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> dict[str, Any]:
    """새 파일들을 저장한 작업의 입력 역할에 맞춘다(읽기 전용 — 기록·파일 없음). 모두 확실하면 요청(request)까지 만들어
    돌려주고(ready), 아니면 역할·열마다 후보와 막힌 이유를 돌려준다 — apply로 사용자가 고른 것을 보낸다."""
    job = _get(db, job_id, user)
    spec = _load_spec(job)
    with output_slot():
        infos = _own_infos(db, user, body.file_ids)
        db.rollback()
    return {"job": job_out(job), **apply_spec(spec, infos, None, {})}


@router.post("/{job_id}/apply")
def apply_job(
    job_id: int, body: ApplyIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> dict[str, Any]:
    """사용자가 고른 배정(역할 → 파일·시트, 정하지 못한 열의 선택)으로 요청을 만든다(읽기 전용). 결과는 기존 연결 확인·
    만들기(plan/run)에 그대로 보낸다 — 새 파일은 거기서 다시 검증한다."""
    job = _get(db, job_id, user)
    spec = _load_spec(job)
    roles = {ip["role"] for ip in spec["inputs"]}
    given = [a.role for a in body.assignments]
    if len(set(given)) != len(given) or set(given) != roles:
        raise _bad(f"입력(역할) {sorted(roles)}마다 파일을 하나씩 정하세요 (보낸 역할 {given}).")
    pairs = [(a.file_id, a.sheet_id) for a in body.assignments]
    if len(set(pairs)) != len(pairs):
        raise _bad("같은 파일·시트를 두 입력에 쓸 수 없습니다.")
    with output_slot():
        infos = _own_infos(db, user, [a.file_id for a in body.assignments])
        db.rollback()
    assignments: dict[int, tuple[int, int]] = {}
    overrides: dict[int, dict[str, str | None]] = {}
    for a in body.assignments:
        info = infos[a.file_id]
        si = int(a.sheet_id[1:])
        if si >= len(info.sheets):
            raise _bad(f"입력 {a.role}: #{a.file_id} 파일에 없는 시트입니다: {a.sheet_id}")
        width = len(info.sheets[si].column_names)
        for cid in a.columns.values():
            if cid is not None and int(cid[1:]) >= width:
                raise _bad(f"입력 {a.role}: 없는 열입니다: {cid}")
        assignments[a.role] = (a.file_id, si)
        overrides[a.role] = dict(a.columns)
    return {"job": job_out(job), **apply_spec(spec, infos, assignments, overrides)}
