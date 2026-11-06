import { useEffect, useId, useRef, useState } from 'react'
import {
  AnalysisProblemError,
  ApiError,
  applyKeyJoinJob,
  createKeyJoinJob,
  deleteKeyJoinJob,
  fetchPreview,
  listKeyJoinJobs,
  matchKeyJoinJob,
  outcomeText,
  outcomeUnknown,
  planKeyJoin,
  runKeyJoin,
  updateKeyJoinJob,
  KEYJOIN_AGG_LABEL,
  type Cell,
  type IntegrationRun,
  type KeyJoinAggFunc,
  type KeyJoinInput,
  type KeyJoinJob,
  type KeyJoinJobAssignment,
  type KeyJoinJobMatch,
  type KeyJoinKinds,
  type KeyJoinPlan,
  type KeyJoinPlanInput,
  type KeyJoinProblem,
  type KeyJoinRequest,
  type KeyJoinRowDecisions,
  type KeyJoinScope,
  type PreviewColumn,
  type SheetPreview,
} from './api'
import { Toggle } from './components/tailgrids/core/toggle'
import PiiNotice from './PiiNotice'
import { navigate, replaceSearch, useSearch } from './router'
import { RunDownloadLink } from './RunDownloadLink'
import { TableRegion } from './Analysis' // 넘칠 때만 이름(region)·Tab 정지점을 받는 표 래퍼 (키보드 가로 스크롤)
import { formatTime, KEYJOIN_SCOPE_LABEL } from './runFormat'
import { Banner, NavLink, WorkflowSteps, type Step } from './Workflow'

const STORE = 'moajwo.keyjoin.draft' // 새로고침·뒤로 가기 뒤에도 같은 파일이면 설정을 이어 쓴다 (이 브라우저에만, 선택은 ID로만)
const MIN_INPUTS = 2 // 서버의 최소 입력 수
const MAX_KEY_COLUMNS = 5 // 서버 MAX_KEY_COLUMNS (확인 응답의 limits.key_columns가 오면 그 값)
const OUT_NAME_MAX = 255 // 서버 OUT_NAME_MAX
const PREVIEW_ROWS = 5 // 시트·열 목록을 읽을 때 함께 받는 가린 행 수 (표시용)
const KEY_HELP =
  "이름이 달라도 됩니다. 정수 123은 글자 '123'과 연결되고 '00123'과는 다릅니다. 날짜·참거짓·소수·16자리 이상 숫자 칸은 쓸 수 없습니다 — 글자(텍스트)로 저장해 올리세요."
const STALE_TEXT = '확인하는 동안 설정이 바뀌어 이전 설정의 결과는 쓰지 않았습니다 — 지금 설정으로 다시 확인하세요.'
const SCOPES: KeyJoinScope[] = ['base', 'all', 'common']
// 같은 요청 번호의 재전송(200)이 돌려준 실행이 아직 끝나지 않았을 때의 상태 글자 (내려받기는 done에만)
const RUN_STATE: Record<string, string> = { running: '만드는 중', check: '결과 확인 필요', failed: '실패', review: '검토 중' }
const REVIEW_PAGE = 50 // '확인이 필요한 행' 표의 한 쪽 행 수
const REASON_TEXT = { subtotal: '소계·합계로 보이는 값', note: '주석으로 보이는 값' } as const
const SCOPE_HELP: Record<KeyJoinScope, string> = {
  base: '기준 파일·시트의 key (기본값) — 다른 파일에 없는 key는 그 파일의 열이 빈 칸',
  all: '모든 파일의 key 합집합 — 기준 파일·시트의 순서 뒤에 나머지에서 처음 나온 순서',
  common: '모든 파일에 다 있는 key만 — 선택한 범위에 없어 제외된 key 수는 확인 뒤에 보입니다',
}

// 입력란은 사용자가 친 글자 그대로(…_input) 두고, 서버에는 보낼 수 있는 값만 보낸다 (buildRequest)
type ColumnDraft = { column_id: string; out_name_input?: string }
// key별 집계 항목 (agg_on일 때만 요청에 들어간다): 행 수는 열이 없다
type AggDraft = { func: KeyJoinAggFunc; column_id: string | null; out_name_input?: string }
// compound: 복합 key 편집 모드 (화면 상태 — 요청에는 key_columns의 개수만 남는다)
// agg_on: 이 입력은 key별로 집계한 뒤 연결 (켜면 가져올 열 대신 aggregate 항목을 보낸다)
type InputDraft = { key_columns: string[]; columns: ColumnDraft[]; compound?: boolean; agg_on?: boolean; aggregate?: AggDraft[] }
const AGG_FUNCS: KeyJoinAggFunc[] = ['count_rows', 'count_values', 'sum', 'max']
const AGG_HELP: Record<KeyJoinAggFunc, string> = {
  count_rows: 'key의 행 수 (열 없음)',
  count_values: '그 열에 값이 있는 행 수 — 빈 칸·결측 표기는 세지 않음',
  sum: '숫자 합 — 값이 없는 key는 빈 칸(0 아님), 숫자가 아닌 값이 있으면 만들지 않음',
  max: '숫자끼리 또는 날짜끼리 최댓값 — 섞이거나 글자면 만들지 않음',
}
type Draft = {
  sheets: Record<string, string> // 파일 번호 → 고른 시트 ID (시트가 하나인 파일은 비워 두면 s0)
  inputs: Record<string, InputDraft> // '파일:시트' → key 열·가져올 열
  scope: KeyJoinScope
  base: string | null // 기준 입력 '파일:시트' (입력에 없으면 첫 입력)
  key_names_input?: string[] // key 결과 이름 (모두 비우면 기준 입력의 key 열 이름)
  rows?: Record<string, KeyJoinRowDecisions> // '파일:시트' → 추정 행의 포함/제외 (원본 행 번호 — 파일·시트가 바뀌면 지움)
}
type InputRef = { fid: number; sid: string; key: string; draft: InputDraft }
// key: 입력('파일:시트')·파일('파일') 또는 전체(null). about: 전체 항목 중 어느 입력란의 문제인지 (그 입력란 옆에 보임)
// agg: key별 집계 항목의 문제 (item = 항목 번호 0부터, 항목이 없을 때는 null · field = 고칠 칸) — 3단계 집계 구역에 보이고 그 칸에 연결한다
type Invalid = { key: string | null; text: string; about?: 'key_names'; agg?: { item: number | null; field: 'func' | 'column' | 'name' } }
type Busy = 'plan' | 'run' | null
type Failure = { message: string; problems: string[]; code: string | null; runId: number | null; unknown: boolean }
type Made = { key: string; run: IntegrationRun; created: boolean }

const EMPTY: Draft = { sheets: {}, inputs: {}, scope: 'base', base: null }
const NEW_INPUT: InputDraft = { key_columns: [], columns: [] }

function filesFrom(search: string): number[] {
  const raw = new URLSearchParams(search).get('files') ?? ''
  return [...new Set(raw.split(',').map((v) => Number(v.trim())))].filter((n) => Number.isInteger(n) && n > 0)
}

function loadDraft(filesKey: string): Draft {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) ?? 'null') as { files: string; draft: Partial<Draft> } | null
    if (!saved || saved.files !== filesKey || !saved.draft) return EMPTY
    const d = saved.draft
    return {
      sheets: d.sheets ?? {},
      inputs: d.inputs ?? {},
      scope: d.scope && SCOPES.includes(d.scope) ? d.scope : 'base',
      base: d.base ?? null,
      key_names_input: d.key_names_input,
      rows: d.rows ?? {},
    }
  } catch {
    return EMPTY
  }
}

function saveDraft(filesKey: string, draft: Draft) {
  try {
    sessionStorage.setItem(STORE, JSON.stringify({ files: filesKey, draft }))
  } catch {
    // 저장소를 쓸 수 없으면(사생활 보호 창 등) 이번 화면에서만 유지한다
  }
}

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

/** 저장한 작업 갱신(PUT)의 결과를 모르는가 — 서버가 거절했다고 확정된 응답(4xx: 409 버전·이름·연결 확인, 404, 422, 401 등)이
 *  아니면 모른다: 응답을 받지 못함(연결 끊김 — TypeError), 응답 본문을 다 받지 못함, 중계·서버의 5xx(502·503·504).
 *  서버는 이미 커밋했을 수 있다 — 격리 API(2026-11-04)에서 요청을 다 보낸 직후 연결이 끊겨도 서버는 처리를 마치고 커밋했다 */
function updateOutcomeUnknown(err: unknown): boolean {
  if (err instanceof AnalysisProblemError) return false
  if (err instanceof ApiError) return err.status >= 500
  return true
}

/** 요청 번호: 같은 생성 요청의 재전송을 서버가 알아보는 32자리 16진수 (randomUUID와 달리 보안 문맥이 아닌 주소에서도 됨) */
function newRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 결과 이름 입력 → 서버에 보낼 값. 앞뒤 공백을 떼고, 비면 서버 기본(null = 원본 열 이름) */
function nameOf(text: string | undefined): string | null {
  const t = (text ?? '').trim()
  return t ? t : null
}

const colText = (c: PreviewColumn | undefined) => (c ? `${c.letter}열 ${c.name}` : '(열 없음)')
const cellText = (c: Cell) => (c === null ? '' : typeof c === 'boolean' ? (c ? 'TRUE' : 'FALSE') : String(c))

function kindsText(k: KeyJoinKinds | null): string {
  if (!k) return '분석 전'
  const parts = [`글자 ${k.text}`, `정수 ${k.int}`, `빈 칸 ${k.empty}`, `병합 채움 ${k.filled}`]
  if (k.float) parts.push(`소수 ${k.float}`)
  if (k.bool) parts.push(`참거짓 ${k.bool}`)
  if (k.date) parts.push(`날짜 ${k.date}`)
  if (k.missing_mark) parts.push(`결측 표기 ${k.missing_mark}`)
  if (k.precision) parts.push(`자릿수 보존 불가 ${k.precision}`)
  if (k.illegal) parts.push(`쓸 수 없는 글자 ${k.illegal}`)
  return parts.join(' · ')
}

function summaryText(p: KeyJoinPlan): string {
  const unlinked = p.inputs.map((i) => `입력 ${i.index + 1} ${i.keys ? i.keys.unlinked.toLocaleString('ko-KR') : '-'}`).join(', ')
  const parts = [`확인 결과: 결과 ${p.result.rows.toLocaleString('ko-KR')}행 · 열 ${p.result.columns}개`, `미연결(입력별) ${unlinked}`]
  if (p.problems.length) parts.push(`만들 수 없는 이유 ${p.problems.length}건`)
  if (p.warnings.length) parts.push(`참고 ${p.warnings.length}건`)
  return `${parts.join(' · ')} — ${p.ready ? '만들 수 있습니다.' : '문제를 해결해야 만들 수 있습니다.'}`
}

/** 파일마다 이번 요청에 들어갈 입력(시트). 시트가 여러 개인 파일은 고를 때까지, 시트 목록을 아직 못 읽은 파일은 읽을 때까지 비운다 */
function inputsOf(
  files: number[],
  draft: Draft,
  meta: Record<number, SheetPreview>,
): { refs: InputRef[]; waiting: number[]; unpicked: number[] } {
  const refs: InputRef[] = []
  const waiting: number[] = []
  const unpicked: number[] = []
  for (const fid of files) {
    const m = meta[fid]
    let sid: string | undefined = draft.sheets[String(fid)]
    if (m && sid && !m.sheets.some((s) => s.sheet_id === sid)) sid = undefined // 파일이 바뀌어 없는 시트
    if (!sid) {
      if (!m) {
        waiting.push(fid)
        continue
      }
      if (m.sheets.length !== 1) {
        unpicked.push(fid)
        continue
      }
      sid = 's0'
    }
    const key = `${fid}:${sid}`
    refs.push({ fid, sid, key, draft: draft.inputs[key] ?? NEW_INPUT })
  }
  return { refs, waiting, unpicked }
}

/** 지금 설정으로 보낼 요청과 고쳐야 할 입력. 빠진 파일·바꾼 시트의 key·열 설정은 보내지 않는다 (서버 422는 최종 방어). */
function buildRequest(
  files: number[],
  draft: Draft,
  meta: Record<number, SheetPreview>,
  metaError: Record<number, string> = {},
) {
  const { refs, waiting, unpicked } = inputsOf(files, draft, meta)
  const invalid: Invalid[] = []
  for (const fid of waiting)
    invalid.push({
      key: String(fid),
      text: metaError[fid]
        ? `#${fid}: 시트·열 목록을 읽지 못했습니다 — 1단계에서 '다시 읽기'를 누르거나 이 파일을 빼세요.`
        : `#${fid}: 시트·열 목록을 읽는 중입니다.`,
    })
  for (const fid of unpicked) invalid.push({ key: String(fid), text: `#${fid}: 연결할 시트를 고르세요.` })
  if (files.length < MIN_INPUTS)
    invalid.push({ key: null, text: `파일이 ${MIN_INPUTS}개 이상 있어야 연결할 수 있습니다 — 홈에서 파일을 더 고르세요.` })
  const k = refs[0]?.draft.key_columns.length ?? 0
  refs.forEach((r, i) => {
    const where = `입력 ${i + 1} (#${r.fid})`
    const n = r.draft.key_columns.length
    if (!n) invalid.push({ key: r.key, text: `${where}: key 열을 고르세요.` })
    else if (n !== k)
      invalid.push({
        key: r.key,
        text: `${where}: key 열 수(${n})가 입력 1(${k})과 다릅니다 — 복합 key는 모든 입력에서 같은 개수·순서여야 합니다.`,
      })
    if (r.draft.columns.some((c) => (c.out_name_input ?? '').trim().length > OUT_NAME_MAX))
      invalid.push({ key: r.key, text: `${where}: 결과 이름은 ${OUT_NAME_MAX}자까지입니다.` })
    if (r.draft.agg_on) {
      const items = r.draft.aggregate ?? []
      if (!items.length)
        invalid.push({ key: r.key, text: `${where}: 집계 항목을 하나 이상 추가하세요 (행 수·값 있는 수·합계·최댓값).`, agg: { item: null, field: 'func' } })
      const seen = new Set<string>()
      items.forEach((a, n) => {
        const cid = a.func === 'count_rows' ? null : a.column_id
        const col = { item: n, field: 'column' } as const
        if (a.func !== 'count_rows' && !cid) invalid.push({ key: r.key, text: `${where}: 집계 항목 ${n + 1}의 열을 고르세요.`, agg: col })
        if (cid && r.draft.key_columns.includes(cid))
          invalid.push({ key: r.key, text: `${where}: 집계 항목 ${n + 1}은 key 열입니다 — 다른 열을 고르세요.`, agg: col })
        const tag = `${a.func}:${cid ?? ''}`
        if (seen.has(tag))
          invalid.push({ key: r.key, text: `${where}: 같은 집계(항목 ${n + 1})를 두 번 넣었습니다.`, agg: { item: n, field: a.func === 'count_rows' ? 'func' : 'column' } })
        seen.add(tag)
        if ((a.out_name_input ?? '').trim().length > OUT_NAME_MAX)
          invalid.push({ key: r.key, text: `${where}: 집계 항목 ${n + 1}의 결과 이름은 ${OUT_NAME_MAX}자까지입니다.`, agg: { item: n, field: 'name' } })
      })
    }
  })
  const names = Array.from({ length: k }, (_, i) => (draft.key_names_input?.[i] ?? '').trim())
  const filled = names.filter(Boolean).length
  let key_names: string[] | null = null
  if (k > 0 && filled === k) key_names = names
  else if (filled > 0)
    invalid.push({ key: null, about: 'key_names', text: 'key 결과 이름은 모두 적거나 모두 비우세요 (비우면 기준 입력의 key 열 이름).' })
  if (names.some((x) => x.length > OUT_NAME_MAX))
    invalid.push({ key: null, about: 'key_names', text: `key 결과 이름은 ${OUT_NAME_MAX}자까지입니다.` })
  const baseKey = draft.base && refs.some((r) => r.key === draft.base) ? draft.base : (refs[0]?.key ?? null)
  const baseRef = refs.find((r) => r.key === baseKey)
  const inputs: KeyJoinInput[] = refs.map((r) => {
    const decided = draft.rows?.[r.key]
    const rows = decided && (decided.include.length || decided.exclude.length) ? { include: [...decided.include].sort((a, b) => a - b), exclude: [...decided.exclude].sort((a, b) => a - b) } : undefined
    const aggregate = r.draft.agg_on
      ? {
          items: (r.draft.aggregate ?? []).map((a) => ({
            func: a.func,
            column_id: a.func === 'count_rows' ? null : a.column_id,
            out_name: nameOf(a.out_name_input),
          })),
        }
      : null
    return {
      file_id: r.fid,
      sheet_id: r.sid,
      key_columns: r.draft.key_columns,
      columns: aggregate
        ? [] // 집계 입력은 집계한 값만 결과에 넣는다 (가져올 열 없음)
        : r.draft.columns
            .filter((c) => !r.draft.key_columns.includes(c.column_id)) // key 열은 결과에 이미 들어간다
            .map((c) => ({ column_id: c.column_id, out_name: nameOf(c.out_name_input) })),
      ...(rows ? { rows } : {}),
      ...(aggregate ? { aggregate } : {}),
    }
  })
  const request: KeyJoinRequest = {
    inputs,
    scope: draft.scope,
    base: baseRef ? { file_id: baseRef.fid, sheet_id: baseRef.sid } : { file_id: 0, sheet_id: 's0' },
    key_names,
  }
  return { request, invalid, refs, baseKey }
}

const without = <T,>(record: Record<number, T>, fid: number): Record<number, T> =>
  Object.fromEntries(Object.entries(record).filter(([k]) => Number(k) !== fid))

/** 저장한 작업을 새 파일에 맞춘 요청 → 화면 초안 (행 포함/제외 결정은 비운다 — 새 파일마다 다시 정한다) */
function draftFromRequest(req: KeyJoinRequest): Draft {
  const sheets: Record<string, string> = {}
  const inputs: Record<string, InputDraft> = {}
  for (const i of req.inputs) {
    sheets[String(i.file_id)] = i.sheet_id
    inputs[`${i.file_id}:${i.sheet_id}`] = {
      key_columns: i.key_columns,
      columns: i.columns.map((c) => ({ column_id: c.column_id, out_name_input: c.out_name ?? '' })),
      compound: i.key_columns.length > 1,
      ...(i.aggregate
        ? { agg_on: true, aggregate: i.aggregate.items.map((a) => ({ func: a.func, column_id: a.column_id, out_name_input: a.out_name ?? '' })) }
        : {}),
    }
  }
  return {
    sheets,
    inputs,
    scope: req.scope,
    base: `${req.base.file_id}:${req.base.sheet_id}`,
    key_names_input: req.key_names ?? undefined,
    rows: {},
  }
}

// 이 화면의 설정과 이어진 저장한 작업 — 갱신(PUT)의 대상. from: 'match' = 맞추기·적용으로 초안에 넣은 작업(변경·예외 배너를 보인다),
// 'save' = 이 화면에서 새로 저장했거나 같은 이름 안내에서 고른 기존 작업 (적용한 것이 아니라 배너는 없다)
type AppliedJob = { job: KeyJoinJob; notes: string[]; kinds: KeyJoinJobMatch['expected_key_kinds']; from: 'match' | 'save' }
type JobFocusTo = 'cancel' | 'ask' | 'sure' | 'recheck' | 'select' | 'legend'
// 같은 이름(409) 뒤 안내 — 저장은 다시 보내지 않고 목록만 다시 읽어 찾은 기존 작업 (못 찾았거나 목록을 읽지 못하면 job = null)
type NameTaken = { name: string; job: KeyJoinJob | null; readError: string | null }

/** 저장 응답의 입력별 key 자료형 분포 → 맞추기 응답(expected_key_kinds)과 같은 꼴 (역할 → 분포) */
function kindsOf(job: KeyJoinJob): AppliedJob['kinds'] {
  return Object.fromEntries(job.inputs.map((i) => [String(i.role), i.key_kinds]))
}

/** 저장한 작업의 마지막 저장 시각 — 갱신한 적이 있으면 수정 시각, 없으면 처음 저장 시각 */
function jobTimeText(job: KeyJoinJob): string {
  return `${job.updated_at ? '수정' : '저장'} ${formatTime(job.updated_at ?? job.created_at)}`
}

/** 저장 때 key 열의 자료형 분포(글자·정수)와 이번 확인 결과를 비교 — 서버(app/keyjoin_jobs.py kind_changes)와 같은 규칙 */
function kindChangeNotes(expected: AppliedJob['kinds'], plan: KeyJoinPlan): string[] {
  const out: string[] = []
  plan.inputs.forEach((ip, i) => {
    const saved = expected[String(i + 1)]
    if (!saved) return
    ip.key_columns.forEach((k, pos) => {
      const was = saved[pos]
      const now = k.kinds
      if (!was || !now) return
      if ((was.int === 0 && now.int > 0) || (was.text === 0 && now.text > 0))
        out.push(
          `입력 ${i + 1} key ${pos + 1}: key 자료형이 저장 때(글자 ${was.text}·정수 ${was.int})와 다릅니다 (이번 글자 ${now.text}·정수 ${now.int}) — 확인 결과를 보고 판단하세요.`,
        )
    })
  })
  return out
}

/** 공통 key로 연결하기: 고른 파일 → (취합 방식: 공통 key 연결) → 파일·시트 → key 열 → 가져올 열 → 범위 → 연결 확인 → 만들기 → 실행 기록.
 * 입력 요소는 항상 그리고(확인 결과로 사라지지 않음) 확인 결과 영역만 마지막으로 받은 결과(view)로 그린다. 만들기는 지금 설정으로
 * 받은 확인 결과(fresh의 basis)가 있고 고칠 입력이 없을 때만 된다. 설정이 바뀌면 다시 확인해야 하고, 늦게 온 확인 응답은 버린다
 * (최신 선택이 이김). 확인·만들기 중에도 버튼을 disabled로 바꾸지 않아(aria-disabled + 무시) 키보드 초점이 움직이지 않는다.
 * 상태 알림(role=status)은 한 곳 — 확인 중·확인 결과·설정 바뀜·만든 결과를 모두 거기에 적는다. */
function KeyJoin() {
  const search = useSearch()
  const files = filesFrom(search)
  const filesKey = files.join(',')
  const [draft, setDraft] = useState<Draft>(() => loadDraft(filesKey))
  const [draftFor, setDraftFor] = useState(filesKey)
  if (draftFor !== filesKey) {
    setDraftFor(filesKey)
    setDraft(loadDraft(filesKey))
  }
  const [meta, setMeta] = useState<Record<number, SheetPreview>>({}) // 파일 보기 API가 준 시트·열 목록 (가린 이름, 표시용)
  const [metaError, setMetaError] = useState<Record<number, string>>({})
  const [metaVersion, setMetaVersion] = useState(0) // '다시 읽기'
  const [checked, setChecked] = useState<{ key: string; plan: KeyJoinPlan } | null>(null)
  const [checkError, setCheckError] = useState<{ key: string; message: string } | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [note, setNote] = useState<{ key: string; text: string } | null>(null) // 상태 알림: 지금 설정에서 일어난 마지막 일
  const [failure, setFailure] = useState<Failure | null>(null)
  const [made, setMade] = useState<Made | null>(null)
  const [pending, setPending] = useState<{ key: string; id: string } | null>(null) // 결과를 모르는 생성 요청 번호 (같은 번호로 다시 보냄)
  const seq = useRef(0)
  const keyRef = useRef('')
  const reqRef = useRef<{ request: KeyJoinRequest; invalid: Invalid[] } | null>(null)
  const metaRef = useRef(meta)
  const busyRef = useRef<Busy>(null)
  const planAbort = useRef<AbortController | null>(null)
  const filesLegendRef = useRef<HTMLLegendElement | null>(null) // 파일을 빼면 사라진 단추 대신 1단계 제목이 초점을 받는다
  const aggAddRefs = useRef<Record<string, HTMLButtonElement | null>>({}) // 집계 항목을 빼면 사라진 '빼기' 대신 그 입력의 '집계 항목 추가'가 초점을 받는다
  const ids = useId()
  // 저장한 작업(설정 재사용): 목록 → 이번 파일에 맞추기(match) → 확실하지 않은 것은 골라 적용(apply) → 초안에 넣기
  const [jobs, setJobs] = useState<KeyJoinJob[] | null>(null)
  const [jobsError, setJobsError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<number | null>(null)
  const [jobMatch, setJobMatch] = useState<KeyJoinJobMatch | null>(null) // 자동으로 정하지 못한 배정·열 (사용자가 고른다)
  const [rolePicks, setRolePicks] = useState<Record<number, string>>({}) // 역할 → '파일:시트'
  const [colPicks, setColPicks] = useState<Record<string, string>>({}) // '역할:k0' → column_id ('' = 가져올 열을 뺌)
  const [jobBusy, setJobBusy] = useState<'match' | 'apply' | 'save' | 'recheck' | 'delete' | null>(null)
  const [jobNote, setJobNote] = useState<string | null>(null) // 저장한 작업 적용 구역의 상태 알림
  const [saveNote, setSaveNote] = useState<string | null>(null) // 6단계 저장 구역의 상태 알림 (목록이 비어 있어도 보인다)
  const [applied, setApplied] = useState<AppliedJob | null>(null) // 이번 초안을 만든 저장한 작업 (변경·예외 안내)
  // 갱신 결과를 확인하지 못했고 저장된 판도 읽지 못함 — 갱신 단추 자리에 '저장된 판 확인'(목록만 다시 읽음)을 둔다.
  // seen: 갱신을 보낸 때의 작업, why: 받은 오류 문구
  const [updateUnknown, setUpdateUnknown] = useState<{ seen: AppliedJob; why: string } | null>(null)
  const [saveName, setSaveName] = useState('')
  // 이 화면이 applied 작업에 마지막으로 저장(새로 저장·갱신)한 설정의 saveKey — 지금 설정과 같으면 6단계 칸이 '저장됨'이 된다
  const [savedKey, setSavedKey] = useState<string | null>(null)
  const [taken, setTaken] = useState<NameTaken | null>(null) // 같은 이름 409 뒤 기존 작업 안내 (자동으로 다시 보내지 않는다)
  const saveNameRef = useRef<HTMLInputElement | null>(null)
  const saveTitleRef = useRef<HTMLParagraphElement | null>(null) // 6단계 저장 칸의 제목 ('저장됨: …'으로 바뀐다)
  const takenRef = useRef<HTMLDivElement | null>(null)
  // 삭제 확인을 연 작업 — 확인 문구·'정말 삭제'·다시 확인은 연 때의 ID에만 (그 뒤 선택·목록이 바뀌어도 대상이 바뀌지 않는다).
  // unknown: 삭제 결과를 확인하지 못함 (응답도 목록도 못 받음) — '정말 삭제' 대신 목록만 다시 읽는 '삭제 여부 다시 확인'
  const [delTarget, setDelTarget] = useState<{ id: number; name: string; unknown: boolean } | null>(null)
  // 이 화면에서 마지막 작업을 지웠으면 구역(제목·빈 상태·상태 알림)을 남긴다 — 처음부터 0개면 지금처럼 그리지 않는다
  const [jobsEmptied, setJobsEmptied] = useState(false)
  // 삭제 확인을 열고 닫거나 지운 뒤, 사라진 단추 대신 초점을 받을 곳 (다시 그린 뒤 옮긴다)
  // force: 사용자가 이 구역 밖의 단추(같은 이름 안내의 '그 작업 고르기')로 직접 옮겨 달라고 한 때 — 초점이 구역 밖에 있어도 옮긴다
  const [jobFocus, setJobFocus] = useState<{ to: JobFocusTo; n: number; force?: boolean } | null>(null)
  const jobsAreaRef = useRef<HTMLFieldSetElement | null>(null)
  const jobsLegendRef = useRef<HTMLLegendElement | null>(null)
  const jobSelectRef = useRef<HTMLSelectElement | null>(null)
  const askDeleteRef = useRef<HTMLButtonElement | null>(null)
  const cancelDeleteRef = useRef<HTMLButtonElement | null>(null)
  const sureDeleteRef = useRef<HTMLButtonElement | null>(null)
  const recheckDeleteRef = useRef<HTMLButtonElement | null>(null)
  const deletingRef = useRef(false) // 다시 그리기 전에 두 번 눌려도 삭제 요청은 한 번
  const mountedRef = useRef(true) // 화면을 떠난 뒤 늦게 온 응답은 상태·초점을 건드리지 않는다
  const jobsSeq = useRef(0) // 저장한 작업 목록 요청 순번 — 늦게 온 예전 응답이 더 새 목록(지운 결과)을 덮지 않게

  const { request, invalid, refs, baseKey } = buildRequest(files, draft, meta, metaError)
  const reqKey = JSON.stringify(request)
  // 저장한 작업에 들어가는 설정만으로 비교하는 key — 행 포함/제외 결정(rows)은 저장하지 않으므로 빼고 본다 ('저장됨' 판정용)
  const saveKey = JSON.stringify({ ...request, inputs: request.inputs.map((i) => ({ ...i, rows: undefined })) })
  // fresh: 지금 설정으로 받은 확인 결과 (만들기의 근거). view: 마지막으로 받은 확인 결과 (결과 영역을 그리는 데만 씀)
  const fresh = checked?.key === reqKey ? checked.plan : null
  const view = checked?.plan ?? null
  const canCheck = files.length >= MIN_INPUTS && invalid.length === 0
  const maxKeys = view?.limits.key_columns ?? MAX_KEY_COLUMNS

  useEffect(() => {
    metaRef.current = meta
  }, [meta])
  useEffect(() => {
    // 늦은 응답 판정과 자동 재확인(개인정보 확인 뒤·만들기 거절 뒤)은 그 시점의 최신 설정을 쓴다
    keyRef.current = reqKey
    reqRef.current = { request, invalid }
  })
  useEffect(() => {
    saveDraft(filesKey, draft)
  }, [filesKey, draft])
  useEffect(() => () => planAbort.current?.abort(), [])
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])
  useEffect(() => {
    if (!jobFocus) return
    // 사용자가 그새 이 구역 밖으로 초점을 옮겼으면 빼앗지 않는다 (사라진 단추의 초점은 body로 떨어져 있다)
    const active = document.activeElement
    if (!jobFocus.force && active && active !== document.body && !jobsAreaRef.current?.contains(active)) return
    const target = {
      cancel: cancelDeleteRef,
      ask: askDeleteRef,
      sure: sureDeleteRef,
      recheck: recheckDeleteRef,
      select: jobSelectRef,
      legend: jobsLegendRef,
    }[jobFocus.to].current
    // 스크롤을 막지 않는다 — 지운 뒤의 선택 상자·제목은 확대(200%)·좁은 화면에서 화면 위쪽 밖에 있을 수 있어, 초점이 보이도록 필요한 만큼만 옮긴다
    target?.focus()
  }, [jobFocus])
  useEffect(() => {
    const controller = new AbortController()
    listKeyJoinJobs(controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return
        setJobs(list)
        setJobId((cur) => (cur !== null && list.some((j) => j.id === cur) ? cur : (list[0]?.id ?? null)))
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setJobsError(`저장한 작업 목록을 읽지 못했습니다: ${errorText(err)}`)
      })
    return () => controller.abort()
  }, [])

  // 파일마다 시트·열 목록을 읽는다 (고른 시트가 바뀌면 그 파일만 다시). 응답이 오기 전에 바뀐 요청은 취소한다
  const sheetsKey = files.map((fid) => `${fid}:${draft.sheets[String(fid)] ?? ''}`).join(',')
  useEffect(() => {
    const controllers: AbortController[] = []
    for (const part of sheetsKey ? sheetsKey.split(',') : []) {
      const [fidText, sid] = part.split(':')
      const fid = Number(fidText)
      const have = metaRef.current[fid]
      if (have && (!sid || have.sheet_id === sid)) continue
      const controller = new AbortController()
      controllers.push(controller)
      fetchPreview(fid, sid || null, 0, PREVIEW_ROWS, controller.signal)
        .then((p) => {
          if (controller.signal.aborted) return
          setMeta((m) => ({ ...m, [fid]: p }))
          setMetaError((e) => (fid in e ? without(e, fid) : e))
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setMetaError((e) => ({ ...e, [fid]: errorText(err) }))
        })
    }
    return () => controllers.forEach((c) => c.abort())
  }, [sheetsKey, metaVersion])

  function update(fn: (d: Draft) => Draft) {
    setFailure(null)
    setDraft(fn)
  }

  function setInput(key: string, fn: (i: InputDraft) => InputDraft) {
    update((d) => ({ ...d, inputs: { ...d.inputs, [key]: fn(d.inputs[key] ?? NEW_INPUT) } }))
  }

  /** 시트를 바꾸면 그 파일의 이전 시트 설정(key·열)은 요청에서 빠지고 저장도 지운다 (다른 시트의 열 ID는 뜻이 다름) */
  function setSheet(fid: number, sid: string) {
    update((d) => {
      const sheets = { ...d.sheets }
      if (sid) sheets[String(fid)] = sid
      else delete sheets[String(fid)]
      const inputs = Object.fromEntries(Object.entries(d.inputs).filter(([k]) => !k.startsWith(`${fid}:`)))
      const rows = Object.fromEntries(Object.entries(d.rows ?? {}).filter(([k]) => !k.startsWith(`${fid}:`))) // 행 결정은 그 시트에만
      return { ...d, sheets, inputs, rows, base: d.base?.startsWith(`${fid}:`) ? null : d.base }
    })
  }

  function setKeys(r: InputRef, keys: string[]) {
    // key 열은 가져올 열에서 뺀다 (결과에 이미 들어감)
    setInput(r.key, (i) => ({ ...i, key_columns: keys, columns: i.columns.filter((c) => !keys.includes(c.column_id)) }))
  }

  function setCompound(r: InputRef, on: boolean) {
    setInput(r.key, (i) => ({ ...i, compound: on, key_columns: on ? i.key_columns : i.key_columns.slice(0, 1) }))
  }

  function moveKey(r: InputRef, index: number, by: number) {
    setInput(r.key, (i) => {
      const next = [...i.key_columns]
      const j = index + by
      if (j < 0 || j >= next.length) return i
      ;[next[index], next[j]] = [next[j], next[index]]
      return { ...i, key_columns: next }
    })
  }

  function togglePick(r: InputRef, cid: string, on: boolean) {
    setInput(r.key, (i) => {
      const rest = i.columns.filter((c) => c.column_id !== cid)
      return { ...i, columns: on ? [...rest, { column_id: cid }] : rest }
    })
  }

  function setOutName(r: InputRef, cid: string, text: string) {
    setInput(r.key, (i) => ({ ...i, columns: i.columns.map((c) => (c.column_id === cid ? { ...c, out_name_input: text } : c)) }))
  }

  function moveColumn(r: InputRef, index: number, by: number) {
    setInput(r.key, (i) => {
      const next = [...i.columns]
      const j = index + by
      if (j < 0 || j >= next.length) return i
      ;[next[index], next[j]] = [next[j], next[index]]
      return { ...i, columns: next }
    })
  }

  /** key별 집계 후 연결 켜기/끄기 — 켜면 가져올 열 대신 집계 항목을 보낸다 (처음 켤 때 '행 수' 항목 하나를 둔다) */
  function setAggOn(r: InputRef, on: boolean) {
    setInput(r.key, (i) => ({ ...i, agg_on: on, aggregate: on && !(i.aggregate ?? []).length ? [{ func: 'count_rows', column_id: null }] : i.aggregate }))
  }

  function addAggItem(r: InputRef) {
    setInput(r.key, (i) => ({ ...i, aggregate: [...(i.aggregate ?? []), { func: 'sum', column_id: null }] }))
  }

  function updateAggItem(r: InputRef, index: number, patch: Partial<AggDraft>) {
    setInput(r.key, (i) => ({ ...i, aggregate: (i.aggregate ?? []).map((a, n) => (n === index ? { ...a, ...patch } : a)) }))
  }

  function removeAggItem(r: InputRef, index: number) {
    setInput(r.key, (i) => ({ ...i, aggregate: (i.aggregate ?? []).filter((_, n) => n !== index) }))
    // 누른 '빼기'는 곧 사라지므로 초점이 body로 떨어지지 않게, 집계를 켠 동안 늘 있는 같은 입력의 '집계 항목 추가'로 바로 옮긴다
    aggAddRefs.current[r.key]?.focus()
  }

  /** 추정 행의 결정: 한 행(또는 여러 행)을 포함/제외/미결정으로. 결정은 요청의 일부라 바꾸면 다시 확인해야 한다 */
  function decideRows(key: string, rows: number[], decision: 'include' | 'exclude' | null) {
    update((d) => {
      const cur = d.rows?.[key] ?? { include: [], exclude: [] }
      const drop = new Set(rows)
      const next = { include: cur.include.filter((r) => !drop.has(r)), exclude: cur.exclude.filter((r) => !drop.has(r)) }
      if (decision) next[decision] = [...next[decision], ...rows]
      return { ...d, rows: { ...(d.rows ?? {}), [key]: next } }
    })
  }

  function setKeyName(index: number, text: string) {
    update((d) => {
      const names = [...(d.key_names_input ?? [])]
      while (names.length <= index) names.push('')
      names[index] = text
      return { ...d, key_names_input: names }
    })
  }

  function removeFile(fid: number) {
    const rest = files.filter((f) => f !== fid)
    const q = new URLSearchParams(window.location.search)
    if (rest.length) q.set('files', rest.join(','))
    else q.delete('files')
    // 남은 파일의 설정은 이어 쓰고, 뺀 파일의 시트·key·열 설정은 지운다 (주소만 고치므로 초점은 옮기지 않음)
    const sheets = Object.fromEntries(Object.entries(draft.sheets).filter(([k]) => k !== String(fid)))
    const inputs = Object.fromEntries(Object.entries(draft.inputs).filter(([k]) => !k.startsWith(`${fid}:`)))
    const rows = Object.fromEntries(Object.entries(draft.rows ?? {}).filter(([k]) => !k.startsWith(`${fid}:`)))
    saveDraft(rest.join(','), { ...draft, sheets, inputs, rows, base: draft.base?.startsWith(`${fid}:`) ? null : draft.base })
    setMeta((m) => without(m, fid))
    setMetaError((e) => without(e, fid))
    replaceSearch(q.toString() ? `?${q.toString()}` : '')
    // 누른 단추는 곧 사라지므로 초점이 body로 떨어지지 않게 같은 단계의 제목으로 먼저 옮긴다 (스크롤은 그대로).
    // 프레임 콜백에 미루면 headless·백그라운드 창에서 늦어질 수 있어 바로 옮긴다 — 제목 요소는 다시 그려도 그대로다
    filesLegendRef.current?.focus({ preventScroll: true })
  }

  function setBusyState(b: Busy) {
    busyRef.current = b
    setBusy(b)
  }

  /** 연결 확인(읽기 전용). 응답이 요청 번호(seq)·설정(reqKey)과 다르면 버린다 — 늦은 응답이 최신 선택을 덮지 않는다 */
  async function check(force = false) {
    if (busyRef.current && !force) return
    const cur = reqRef.current
    if (!cur || cur.invalid.length) return
    const key = keyRef.current
    const mine = ++seq.current
    planAbort.current?.abort()
    const controller = new AbortController()
    planAbort.current = controller
    setBusyState('plan')
    setNote(null)
    try {
      const plan = await planKeyJoin(cur.request, controller.signal)
      if (mine !== seq.current) return // 더 새 확인이 진행 중 — 그 결과가 화면을 정한다
      if (key !== keyRef.current) {
        setNote({ key: keyRef.current, text: STALE_TEXT })
        return
      }
      setChecked({ key, plan })
      setCheckError(null)
      setNote({ key, text: summaryText(plan) })
    } catch (err) {
      if (controller.signal.aborted || mine !== seq.current) return
      if (key !== keyRef.current) {
        setNote({ key: keyRef.current, text: STALE_TEXT })
        return
      }
      const message = errorText(err)
      setCheckError({ key, message })
      setNote({ key, text: `연결을 확인하지 못했습니다: ${message}` })
    } finally {
      if (mine === seq.current) setBusyState(null)
    }
  }

  /** 엑셀 만들기. resend: 결과를 모르는 요청을 같은 요청 번호로 다시 보냄 (서버가 기존 실행을 200으로 돌려줌). 새 클릭은 새 번호.
   * 같은 설정으로 이미 만들었으면 기본 단추는 다시 만들지 않는다 — again(명시적 '한 번 더')만 새 번호·새 실행 */
  async function make(resend: boolean, again = false) {
    if (busyRef.current || !fresh || !fresh.ready || invalid.length) return
    if (made?.key === reqKey && !resend && !again) return
    const key = reqKey
    const id = resend && pending?.key === key ? pending.id : newRequestId()
    setBusyState('run')
    setFailure(null)
    setPending({ key, id })
    let recheck = false
    try {
      const { run, created } = await runKeyJoin(request, fresh.basis, id)
      setMade({ key, run, created })
      setPending(null)
      setNote({
        key,
        text: created
          ? `실행 #${run.id} 완료 — 결과 ${run.row_count.toLocaleString('ko-KR')}행. 7단계에서 내려받을 수 있습니다.`
          : `실행 #${run.id}는 같은 요청 번호로 이미 만든 실행입니다 — 새로 만들지 않았습니다. 7단계에서 내려받을 수 있습니다.`,
      })
    } catch (err) {
      if (err instanceof AnalysisProblemError) {
        // 409: 확인 뒤 바뀜·문제 있음·요청 번호 불일치/재사용·생성 실패 — 실행을 새로 만들지 않았고, 지금 설정으로 한 번 다시 확인한다
        setPending(null)
        setFailure({ message: err.message, problems: err.problems, code: err.code, runId: err.runId, unknown: false })
        setNote({ key, text: `만들지 못했습니다: ${err.message}` })
        recheck = true
      } else {
        const unknown = outcomeUnknown(err)
        if (!unknown) setPending(null) // 시작하지 않은 요청(차례 없음 등)은 다음에 새 번호로
        const message = outcomeText(err, '만들지 못했습니다: ')
        setFailure({ message, problems: [], code: null, runId: null, unknown })
        setNote({ key, text: message })
      }
    } finally {
      setBusyState(null)
    }
    if (recheck) void check()
  }

  /** 목록을 다시 읽는다. pick: 방금 저장한 작업을 고를 때 — 그 밖에는 지금 선택을 지킨다 (기다리는 동안 사용자가 고른 것을 덮지 않음).
   *  더 나중에 보낸 목록 요청이 있으면 이 응답은 버린다 — 늦게 온 예전 목록이 지운 작업을 되살리지 않게 */
  function reloadJobs(pick: number | null) {
    const n = ++jobsSeq.current
    listKeyJoinJobs()
      .then((list) => {
        if (!mountedRef.current || n !== jobsSeq.current) return
        setJobs(list)
        setJobId((cur) => {
          const has = (id: number | null) => id !== null && list.some((j) => j.id === id)
          return has(pick) ? pick : has(cur) ? cur : (list[0]?.id ?? null)
        })
      })
      .catch((err: unknown) => {
        if (mountedRef.current && n === jobsSeq.current) setJobsError(`저장한 작업 목록을 읽지 못했습니다: ${errorText(err)}`)
      })
  }

  /** 맞춘 요청을 초안으로 — 입력 순서(역할 순)가 주소의 파일 순서가 되도록 주소를 고친다. 행 결정·확인 결과는 새로 시작 */
  function adoptMatch(m: KeyJoinJobMatch) {
    if (!m.request) return
    const req = m.request
    const d = draftFromRequest(req)
    const order = [...req.inputs.map((i) => i.file_id), ...files.filter((f) => !req.inputs.some((i) => i.file_id === f))]
    const key = order.join(',')
    setFailure(null)
    setChecked(null)
    setCheckError(null)
    setNote(null)
    setJobMatch(null)
    setApplied({ job: m.job, notes: m.notes, kinds: m.expected_key_kinds, from: 'match' })
    setSavedKey(null) // 맞춘 초안은 이 화면이 저장한 설정이 아니다 ('저장됨'이 아님)
    setTaken(null)
    setUpdateUnknown(null) // 방금 서버에서 받은 판으로 다시 맞췄다
    setJobNote(`저장한 작업 '${m.job.name}' (v${m.job.version})을 이번 파일에 맞춰 넣었습니다 — 변경·예외 ${m.notes.length}건. 5단계에서 연결을 확인하세요.`)
    if (key !== filesKey) {
      saveDraft(key, d)
      replaceSearch(`?files=${key}`)
    } else {
      setDraft(d)
    }
  }

  /** 저장한 작업을 이번 파일들에 맞춘다 (읽기 전용). 모두 확실하면 바로 초안에 넣고, 아니면 고를 것을 보인다 */
  async function matchJob() {
    if (jobId === null || jobBusy !== null) return
    setJobBusy('match')
    setJobNote(null)
    setJobsError(null)
    try {
      const m = await matchKeyJoinJob(jobId, files)
      if (m.ready) adoptMatch(m)
      else {
        setJobMatch(m)
        setRolePicks(Object.fromEntries(m.roles.map((r) => [r.role, r.file_id !== null ? `${r.file_id}:${r.sheet_id}` : ''])))
        setColPicks({})
        setJobNote(`자동으로 정하지 못한 것이 있습니다 — 아래에서 고른 뒤 '고른 대로 적용'을 누르세요 (${m.blocked.length}건).`)
      }
    } catch (err) {
      setJobsError(`맞추지 못했습니다: ${errorText(err)}`)
    } finally {
      setJobBusy(null)
    }
  }

  /** 사용자가 고른 배정·열로 적용한다 */
  async function applyPicks() {
    if (!jobMatch || jobBusy !== null) return
    const assignments: KeyJoinJobAssignment[] = []
    for (const r of jobMatch.roles) {
      const pick = rolePicks[r.role] ?? ''
      if (!pick) {
        setJobsError(`입력 ${r.role}의 파일을 고르세요.`)
        return
      }
      const [fidText, sid] = pick.split(':')
      const columns: Record<string, string | null> = {}
      for (const [k, v] of Object.entries(colPicks)) {
        const [roleText, tag] = k.split(':')
        if (Number(roleText) === r.role) columns[tag] = v === '' ? null : v
      }
      assignments.push({ role: r.role, file_id: Number(fidText), sheet_id: sid, columns })
    }
    setJobBusy('apply')
    setJobsError(null)
    try {
      const m = await applyKeyJoinJob(jobMatch.job.id, assignments)
      if (m.ready) adoptMatch(m)
      else {
        setJobMatch(m)
        setJobNote(`아직 정하지 못한 것이 있습니다 (${m.blocked.length}건) — 아래 목록을 보고 고르세요.`)
      }
    } catch (err) {
      setJobsError(`적용하지 못했습니다: ${errorText(err)}`)
    } finally {
      setJobBusy(null)
    }
  }

  /** 지금 설정을 저장한 작업으로 저장(target = null: 새 이름) 또는 target 작업을 갱신(버전 +1). 서버가 연결 확인을 한 번 거친다.
   *  저장·갱신에 성공하면 그 작업이 이 화면의 갱신 대상이 되고 6단계 칸이 '저장됨'이 된다 (같은 설정으로 다시 저장하지 않게).
   *  같은 이름(409)이면 다시 보내지 않고 목록만 다시 읽어 기존 작업을 찾아 고를 것(그 작업 고르기·지금 설정으로 갱신·다른 이름)을 보인다 */
  async function saveJob(target: AppliedJob | null) {
    if (jobBusy !== null || invalid.length || files.length < MIN_INPUTS) return
    const name = saveName.trim()
    if (target === null && !name) {
      setSaveNote('저장할 작업 이름을 적으세요.')
      return
    }
    if (target !== null && target.job.id === applied?.job.id && savedKey === saveKey) {
      setSaveNote(`지금 설정은 이미 작업 '${target.job.name}' (v${target.job.version})에 저장되어 있습니다 — 설정을 바꾼 뒤 갱신하세요.`)
      return
    }
    setJobBusy('save')
    setSaveNote(null)
    setUpdateUnknown(null)
    closeTaken()
    const key = saveKey
    try {
      const job =
        target !== null
          ? await updateKeyJoinJob(target.job.id, { expected_version: target.job.version, request })
          : await createKeyJoinJob(name, request)
      setSaveNote(`저장한 작업 '${job.name}' (v${job.version})${target !== null ? '으로 갱신' : '으로 저장'}했습니다 — 다음 자료는 위 '저장한 작업 적용'에서 맞춰 쓰세요.`)
      setApplied(target !== null ? { ...target, job } : { job, notes: [], kinds: kindsOf(job), from: 'save' })
      setSavedKey(key)
      setSaveName('')
      reloadJobs(job.id)
    } catch (err) {
      const stale = err instanceof AnalysisProblemError && err.code === 'KEYJOIN_JOB_VERSION_CHANGED'
      const gone = err instanceof ApiError && err.status === 404
      if (target !== null && (stale || gone)) {
        await showLatestJob(target, null) // 다시 보내지 않는다 — 최신 목록을 읽어 알릴 뿐
        return
      }
      if (target !== null && updateOutcomeUnknown(err)) {
        await showLatestJob(target, errorText(err)) // 결과를 모름 — 다시 보내지 않고, 지금 저장된 판만 읽어 사실대로 알린다
        return
      }
      if (target === null && err instanceof AnalysisProblemError && err.code === 'KEYJOIN_JOB_NAME_TAKEN') {
        setSaveNote(`저장하지 못했습니다: ${err.message} (아래 안내에서 기존 작업을 고르거나 다른 이름을 적으세요)`)
        await showTaken(name) // 다시 보내지 않는다 — 목록만 읽어 기존 작업을 찾는다
        return
      }
      const text =
        err instanceof AnalysisProblemError && err.problems.length ? `${err.message} ${err.problems.join(' / ')}` : errorText(err)
      setSaveNote(`저장하지 못했습니다: ${text}`)
    } finally {
      setJobBusy(null)
    }
  }

  /** 갱신 뒤 최신 목록을 다시 읽어 알린다 — 갱신은 다시 보내지 않는다. 지금 판을 기준으로 다시 누를지는 사용자가 정한다
   *  (재발급의 expected_auth_version과 같은 흐름). 더 나중에 보낸 목록 요청이 있으면 이 응답은 버린다.
   *  unknown = null: 갱신이 거절됨(409: 그 사이 다른 곳에서 먼저 갱신 · 404: 지워짐) — 서버는 아무것도 바꾸지 않았다.
   *  unknown = 받은 오류 문구: 응답으로 결과를 알 수 없음 — 서버가 반영했을 수 있다. 읽은 판만 사실대로 말하고, 버전이 올랐어도
   *  이 화면의 갱신이 반영됐다고 단정하지 않는다(다른 창도 갱신할 수 있다). 읽기도 실패하면 미확인으로 두고 '저장된 판 확인'을 준다 */
  async function showLatestJob(seen: AppliedJob, unknown: string | null) {
    const n = ++jobsSeq.current
    setSavedKey(null) // 갱신이 거절됐거나 결과를 모른다 — 이 화면의 설정이 저장됐다고 말하지 않는다
    const head = unknown === null ? '갱신하지 않았습니다' : `갱신 결과를 확인하지 못했습니다 (${unknown}) — 갱신을 다시 보내지 않았습니다.`
    try {
      const list = await listKeyJoinJobs()
      if (!mountedRef.current || n !== jobsSeq.current) return
      setJobs(list)
      setJobId((cur) => (cur !== null && list.some((j) => j.id === cur) ? cur : (list[0]?.id ?? null)))
      setUpdateUnknown(null)
      const latest = list.find((j) => j.id === seen.job.id)
      if (!latest) {
        setApplied(null)
        if (list.length === 0) setJobsEmptied(true)
        setSaveNote(
          unknown === null
            ? `갱신하지 않았습니다 — 작업 '${seen.job.name}': 그 사이 지워져 목록에 없습니다. 이 화면의 설정은 새 이름으로 저장할 수 있습니다.`
            : `${head} 다시 읽어 보니 작업 '${seen.job.name}': 지금 목록에 없습니다(지워짐). 이 화면의 설정은 그대로 있고 새 이름으로 저장할 수 있습니다.`,
        )
        return
      }
      setApplied({ ...seen, job: latest })
      if (unknown === null) {
        setSaveNote(
          `갱신하지 않았습니다 — 작업 '${latest.name}': 그 사이 다른 곳에서 먼저 바뀌었습니다 (이 화면이 본 판 v${seen.job.version}, 지금 v${latest.version}). ` +
            `최신 목록을 다시 읽었습니다. 지금 저장된 설정을 보려면 위 '저장한 작업 적용'에서 다시 맞추고, ` +
            `이 화면의 설정으로 지금 판(v${latest.version})을 덮으려면 갱신을 다시 누르세요.`,
        )
        return
      }
      setSaveNote(
        latest.version === seen.job.version
          ? `${head} 다시 읽어 보니 작업 '${latest.name}': 지금 저장된 판은 v${latest.version}입니다 — 이 화면이 갱신을 보낸 판과 같습니다` +
              `(읽은 때까지 반영되지 않음). 이 화면의 설정은 그대로 있습니다. 저장하려면 갱신을 다시 누르세요 — 그 사이 앞의 갱신이 ` +
              `반영됐다면 다시 누른 갱신은 막히고 알려 드립니다.`
          : `${head} 다시 읽어 보니 작업 '${latest.name}': 지금 저장된 판은 v${latest.version}입니다 (이 화면이 갱신을 보낸 판 v${seen.job.version}). ` +
              `이 판이 이 화면의 갱신인지 다른 곳의 변경인지는 알 수 없습니다. 지금 저장된 설정을 보려면 위 '저장한 작업 적용'에서 다시 ` +
              `맞추고, 이 화면의 설정으로 지금 판(v${latest.version})을 덮으려면 갱신을 누르세요.`,
      )
    } catch (err) {
      if (!mountedRef.current || n !== jobsSeq.current) return
      if (unknown === null) {
        setSaveNote(`갱신하지 않았습니다 — 그 사이 다른 곳에서 바뀌었거나 지워졌습니다. 최신 목록을 읽지 못했습니다: ${errorText(err)}`)
        return
      }
      setUpdateUnknown({ seen, why: unknown })
      setSaveNote(
        `${head} 저장된 판도 읽지 못했습니다 (${errorText(err)}). 이 화면의 설정은 그대로 있습니다 — '저장된 판 확인'으로 다시 읽은 뒤 정하세요.`,
      )
    }
  }

  /** '저장된 판 확인' — 결과를 모르는 갱신 뒤 목록만 다시 읽는다 (갱신은 보내지 않는다) */
  async function recheckUpdate() {
    if (updateUnknown === null || jobBusy !== null) return
    setJobBusy('recheck')
    try {
      await showLatestJob(updateUnknown.seen, updateUnknown.why)
    } finally {
      if (mountedRef.current) setJobBusy(null)
    }
  }

  /** 같은 이름(409) 뒤: 저장을 다시 보내지 않고 목록만 다시 읽어 그 이름의 기존 작업을 찾는다 (이 화면의 목록도 최신으로).
   *  더 나중에 보낸 목록 요청이 있으면 이 응답은 버린다 */
  async function showTaken(name: string) {
    const n = ++jobsSeq.current
    try {
      const list = await listKeyJoinJobs()
      if (!mountedRef.current || n !== jobsSeq.current) return
      setJobs(list)
      setJobId((cur) => (cur !== null && list.some((j) => j.id === cur) ? cur : (list[0]?.id ?? null)))
      setTaken({ name, job: list.find((j) => j.name === name) ?? null, readError: null })
    } catch (err) {
      if (!mountedRef.current || n !== jobsSeq.current) return
      setTaken({ name, job: null, readError: errorText(err) })
    }
  }

  /** 같은 이름 안내 — '그 작업 고르기': 위 '저장한 작업 적용'의 선택 상자에서 그 작업을 고르고 초점을 옮긴다 (맞추기는 사용자가 누른다) */
  function pickTaken() {
    if (!taken?.job || jobBusy !== null) return
    const job = taken.job
    setJobId(job.id)
    setJobMatch(null)
    setDelTarget(null)
    setJobsError(null)
    setJobNote(`골랐습니다: 작업 '${job.name}' (v${job.version}) — '이 파일들에 맞추기'를 누르면 이번 파일에 맞춥니다.`)
    requestJobFocus('select', true)
  }

  /** 같은 이름 안내를 닫는다 — 초점이 그 안의 단추에 있었으면(사라진다) 6단계 칸의 제목으로 옮긴다 (body로 떨어지지 않게) */
  function closeTaken() {
    if (takenRef.current?.contains(document.activeElement)) saveTitleRef.current?.focus()
    setTaken(null)
  }

  /** 같은 이름 안내 — '다른 이름 적기': 이름 칸으로 초점을 옮기고 적은 이름을 고른다 (저장은 사용자가 다시 누른다) */
  function focusSaveName() {
    saveNameRef.current?.focus()
    saveNameRef.current?.select()
  }

  function requestJobFocus(to: JobFocusTo, force = false) {
    setJobFocus((f) => ({ to, n: (f?.n ?? 0) + 1, force }))
  }

  /** 인라인 삭제 확인: 누른 '이 작업 삭제'는 사라지므로 덜 위험한 '취소'가 초점을 받는다. 대상은 지금 고른 작업의 ID로 고정 */
  function openDelete() {
    if (jobId === null || jobBusy !== null) return
    setJobsError(null)
    setDelTarget({ id: jobId, name: jobs?.find((j) => j.id === jobId)?.name ?? '', unknown: false })
    requestJobFocus('cancel')
  }

  function cancelDelete() {
    if (jobBusy === 'delete') return // 요청을 보낸 뒤에는 취소할 수 없다 (결과를 기다린다)
    setDelTarget(null)
    requestJobFocus('ask')
  }

  /** 지운 것이 확인됨 — 남은 목록으로 바꾸고 안내·초점 */
  function showDeleted(target: { id: number }, rest: KeyJoinJob[], note: string) {
    setJobs(rest)
    setJobId((cur) => (cur !== null && cur !== target.id && rest.some((j) => j.id === cur) ? cur : (rest[0]?.id ?? null)))
    setDelTarget(null)
    setJobMatch(null)
    if (applied?.job.id === target.id) {
      setApplied(null)
      setUpdateUnknown(null)
      setSavedKey(null)
    }
    if (taken?.job?.id === target.id) setTaken(null)
    if (rest.length === 0) setJobsEmptied(true)
    setJobNote(`${note} 실행 기록·결과 파일은 그대로입니다.${rest.length === 0 ? ' 남은 저장한 작업이 없습니다.' : ''}`)
    // 남은 작업이 있으면 그 선택 상자, 없으면 이 구역의 제목 — 사라진 '정말 삭제' 대신 (안내 문구로는 옮기지 않는다)
    requestJobFocus(rest.length ? 'select' : 'legend')
  }

  /** 삭제 응답이 실패·유실됐을 때(err) 또는 '삭제 여부 다시 확인'(err = null): DELETE는 다시 보내지 않고 목록만 다시 읽어
   *  그 작업이 서버에 남았는지로 결과를 정한다 — 서버가 지운 뒤 응답만 잃었거나 다른 창에서 먼저 지웠을(404) 수 있다 */
  async function settleDelete(target: { id: number; name: string }, err: unknown) {
    jobsSeq.current += 1 // 이보다 먼저 보낸 목록 요청의 늦은 응답은 버린다
    const why = err === null ? '' : ` (삭제 응답: ${errorText(err)})`
    let list: KeyJoinJob[]
    try {
      list = await listKeyJoinJobs()
    } catch (readErr) {
      if (!mountedRef.current) return
      setDelTarget({ ...target, unknown: true })
      setJobsError(
        `삭제 여부를 확인하지 못했습니다${why} — 목록도 읽지 못했습니다(${errorText(readErr)}). '삭제 여부 다시 확인'은 목록만 다시 읽습니다 (삭제 요청을 다시 보내지 않음).`,
      )
      requestJobFocus('recheck')
      return
    }
    if (!mountedRef.current) return
    if (list.some((j) => j.id === target.id)) {
      // 서버에 남아 있다 — 확인('정말 삭제'로 다시 시도·취소)은 그대로
      setJobs(list)
      setDelTarget({ ...target, unknown: false })
      setJobsError(`지우지 못했습니다${why} — 목록을 다시 읽어 보니 작업은 그대로 있습니다. 다시 시도하거나 취소하세요.`)
      requestJobFocus('sure')
    } else {
      showDeleted(target, list, `'${target.name}' 작업은 지워져 목록에 없습니다 — 목록을 다시 읽어 확인했습니다${why}. 다른 창에서 먼저 지웠을 수도 있습니다.`)
    }
  }

  async function removeJob() {
    const target = delTarget
    if (target === null || target.unknown || jobBusy !== null || deletingRef.current) return
    deletingRef.current = true
    setJobBusy('delete')
    setJobsError(null)
    setJobNote(null)
    try {
      try {
        await deleteKeyJoinJob(target.id)
      } catch (err) {
        if (mountedRef.current) await settleDelete(target, err)
        return
      }
      if (!mountedRef.current) return
      showDeleted(target, (jobs ?? []).filter((j) => j.id !== target.id), `저장한 작업을 지웠습니다('${target.name}').`)
      reloadJobs(null)
    } finally {
      deletingRef.current = false
      if (mountedRef.current) setJobBusy(null)
    }
  }

  /** 결과를 확인하지 못한 삭제: 목록만 다시 읽는다 (DELETE를 다시 보내지 않음) */
  async function recheckDelete() {
    const target = delTarget
    if (target === null || jobBusy !== null || deletingRef.current) return
    deletingRef.current = true
    setJobBusy('delete')
    setJobsError(null)
    try {
      await settleDelete(target, null)
    } finally {
      deletingRef.current = false
      if (mountedRef.current) setJobBusy(null)
    }
  }

  const metaOf = (r: InputRef) => {
    const m = meta[r.fid]
    return m && m.sheet_id === r.sid ? m : undefined
  }
  const viewInput = (r: InputRef): KeyJoinPlanInput | undefined =>
    view?.inputs.find((i) => i.file_id === r.fid && i.sheet_id === r.sid)
  function inputLabel(r: InputRef, i: number): string {
    const m = meta[r.fid]
    const tab = m?.sheets.findIndex((s) => s.sheet_id === r.sid) ?? -1
    const sheet = m && tab >= 0 ? ` / ${m.sheets[tab].name} (${tab + 1}번째 시트)` : ''
    return `입력 ${i + 1} · #${r.fid} ${m?.original_name ?? ''}${sheet}`
  }
  const keyCount = refs[0]?.draft.key_columns.length ?? 0
  const keyNameProblems = invalid.filter((x) => x.about === 'key_names')
  const keyNameIds = keyNameProblems.map((_, n) => `${ids}-kn-${n}`)
  const baseRef = refs.find((r) => r.key === baseKey)
  const baseMeta = baseRef ? metaOf(baseRef) : undefined

  const kindNotes = applied && fresh ? kindChangeNotes(applied.kinds, fresh) : []
  const canSave = files.length >= MIN_INPUTS && invalid.length === 0 && jobBusy === null
  // 지금 설정이 applied 작업에 저장되어 있다 (이 화면이 저장·갱신한 그대로) — 6단계 칸은 '저장됨', 갱신은 설정을 바꾼 뒤에만
  const savedHere = applied !== null && savedKey === saveKey
  // 같은 이름 안내의 '지금 설정으로 갱신' 대상 — 그 작업이 이미 이 화면의 작업이면 그 정보(배너 등)를 이어 쓴다
  const takenTarget: AppliedJob | null = taken?.job
    ? applied?.job.id === taken.job.id
      ? { ...applied, job: taken.job }
      : { job: taken.job, notes: [], kinds: kindsOf(taken.job), from: 'save' }
    : null
  const takenSaved = takenTarget !== null && savedHere && applied?.job.id === takenTarget.job.id && applied.job.version === takenTarget.job.version
  const fileLabel = (fid: number) => `#${fid} ${meta[fid]?.original_name ?? ''}`.trim()

  let why: string | null = null
  if (!files.length) why = '고른 파일이 없습니다 — 홈에서 파일을 고르세요.'
  else if (files.length < MIN_INPUTS) why = `입력이 ${MIN_INPUTS}개 미만입니다 — 홈에서 파일을 더 고르세요.`
  else if (invalid.length) why = `고칠 입력이 있습니다 — ${invalid[0].text}`
  else if (checkError?.key === reqKey) why = '연결을 확인하지 못했습니다 — 5단계의 오류를 확인하세요.'
  else if (!fresh) why = view ? '설정을 바꿨습니다 — 5단계에서 다시 확인하세요.' : '5단계에서 연결을 먼저 확인하세요.'
  else if (fresh.problems.length) why = `만들 수 없는 이유 ${fresh.problems.length}건을 먼저 해결하세요 (5단계 목록).`
  else if (fresh.result.rows === 0) why = '연결 결과가 0행입니다 — 범위나 key 열을 바꾸세요.'
  else if (!fresh.ready) why = '지금 설정으로는 만들 수 없습니다 (5단계 확인 결과).'
  // 같은 설정으로 이미 만든 실행이 있으면 기본 단추는 막는다 — 다시 누르면 같은 결과가 하나 더 생기므로 '한 번 더'는 따로 고른다
  const madeHere = made?.key === reqKey ? made : null
  const madeFinal = madeHere !== null && madeHere.run.status === 'done'
  const madeWhy = madeHere
    ? madeFinal
      ? `이 설정으로 이미 만들었습니다 (실행 #${madeHere.run.id}) — 7단계에서 내려받으세요. 같은 설정으로 한 번 더 만들려면 아래 단추를 누르세요.`
      : `실행 #${madeHere.run.id}는 아직 '${RUN_STATE[madeHere.run.status] ?? madeHere.run.status}' 상태입니다 — 실행 기록에서 확인하세요 (새로 만들지 않음).`
    : null

  // 상태 알림 한 곳: 진행 중 > 지금 설정의 마지막 일 > 설정 바뀜. 글자가 바뀔 때만 읽힌다 (입력마다 바뀌지 않음)
  const statusText =
    busy === 'plan'
      ? '연결을 확인하는 중… (파일은 만들지 않음)'
      : busy === 'run'
        ? '결과 파일을 만드는 중… 응답이 올 때까지 기다리세요.'
        : note && note.key === reqKey
          ? note.text
          : view && !fresh
            ? '설정을 바꿨습니다 — 다시 확인해야 만들 수 있습니다.'
            : ''

  const steps: Step[] = [
    { key: 'files', state: files.length ? 'done' : 'todo', detail: `${files.length}개`, href: `/?files=${filesKey}` },
    { key: 'mode', state: 'done', detail: '공통 key 연결', href: '/' },
    {
      key: 'settings',
      state: !files.length ? 'todo' : invalid.length ? 'attention' : 'done',
      detail: `입력 ${refs.length}개 · key 열 ${keyCount || '-'}개 · ${KEYJOIN_SCOPE_LABEL[draft.scope]}`,
      current: true,
    },
    {
      key: 'compose',
      state: !fresh ? 'todo' : why ? 'attention' : 'done',
      detail: fresh ? `결과 ${fresh.result.rows.toLocaleString('ko-KR')}행 · 열 ${fresh.result.columns}개` : undefined,
    },
    { key: 'make', state: made?.key === reqKey ? 'done' : 'todo', detail: made?.key === reqKey ? `실행 #${made.run.id}` : undefined },
  ]

  const toRuns = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault()
    navigate('/runs', { focus: { to: 'page' } })
  }

  return (
    <section className="keyjoin" aria-label="공통 key로 연결하기">
      <WorkflowSteps steps={steps} label="공통 key로 연결하기 단계" />
      <Banner tone="info" icon="info" title="공통 key로 같은 대상의 자료를 연결하고, 파일별로 고른 열을 모읍니다 — 유사 일치·공백 제거·대소문자 통일은 하지 않습니다">
        <p>
          key는 같은 대상을 구분하는 값입니다. 사번·기관코드·업무번호 등을 사용할 수 있습니다. 입력(파일·시트)마다 key 열을 고르면 서버가
          변환 전 값으로 정확히 같은 key만 연결해 key마다 한 행을 만듭니다. 한 입력 안에
          같은 key가 두 번 있으면 만들지 않습니다 (합치거나 고르지 않음). 원본 파일·열 매핑·학습은 바꾸지 않습니다. 화면·기록에는
          가린 값만 보이고 key 원문은 남지 않습니다.
        </p>
      </Banner>

      {!files.length && (
        <Banner tone="warn" icon="alert" title="고른 파일이 없습니다">
          <p>
            홈의 파일 목록에서 파일을 {MIN_INPUTS}개 이상 고르고 결과 형태로 &lsquo;공통 key로 연결하기&rsquo;를 고르세요.{' '}
            <NavLink href="/">홈으로</NavLink>
          </p>
        </Banner>
      )}

      {/* 파일을 고르지 않아도 저장한 작업이 어디 있는지·무엇이 있는지 보이게 (보기만 — 적용·삭제는 파일을 고른 뒤 위 구역에서) */}
      {!files.length && (jobs !== null || jobsError) && (
        <fieldset className="an-step bd-step">
          <legend>저장한 작업{jobs !== null ? ` (${jobs.length}개)` : ''}</legend>
          <p className="mw-muted">
            이 화면에서 저장한 공통 key 연결 설정입니다 (본인 것만 — &lsquo;자동화&rsquo; 메뉴와는 따로 저장). 이번 파일에 맞춰 쓰거나
            지우려면 홈에서 파일을 {MIN_INPUTS}개 이상 고르고 &lsquo;공통 key로 연결하기&rsquo;로 들어오세요 — 위쪽 &lsquo;저장한 작업
            적용&rsquo;에서 합니다.
          </p>
          {jobs !== null &&
            (jobs.length === 0 ? (
              <p className="mw-muted">저장한 작업이 없습니다 — 파일을 고른 뒤 6단계 &lsquo;저장한 작업으로 저장&rsquo;으로 만들 수 있습니다.</p>
            ) : (
              <ul className="bd-files kj-joblist" aria-label="저장한 작업 목록">
                {jobs.map((j) => (
                  <li key={j.id}>
                    <strong>{j.name}</strong>
                    <span>v{j.version}</span>
                    <span>{jobTimeText(j)}</span>
                    <span className="mw-muted">
                      입력 {j.input_count}개 · key 열 {j.key_columns}개 · {j.scope_label}
                    </span>
                  </li>
                ))}
              </ul>
            ))}
          {jobsError && (
            <p className="upload-error" role="alert">
              {jobsError}
            </p>
          )}
        </fieldset>
      )}

      {files.length > 0 && jobs !== null && (jobs.length > 0 || jobsEmptied) && (
        <fieldset ref={jobsAreaRef} className="an-step bd-step" aria-busy={jobBusy === 'match' || jobBusy === 'apply'}>
          {/* 마지막 작업을 지우면 사라진 단추 대신 이 제목이 초점을 받는다 (구역과 상태 알림은 남는다) */}
          <legend ref={jobsLegendRef} tabIndex={-1}>
            저장한 작업 적용 (선택)
          </legend>
          {jobs.length === 0 ? (
            <p className="mw-muted">
              남은 저장한 작업이 없습니다 — 6단계의 &lsquo;저장한 작업으로 저장&rsquo;으로 지금 설정을 다시 저장할 수 있습니다.
            </p>
          ) : (
            <>
              <p className="mw-muted">
                이전에 저장한 연결 설정을 이번 파일들에 맞춥니다 — 파일 이름이 아니라 시트·열 구조(key 열·가져올 열 이름)로 맞추고, 확실하지
                않은 것(맞는 파일이 둘 이상·같은 이름의 열·없는 열)은 묻습니다. 적용한 뒤에도 5단계 연결 확인을 거쳐야 만들 수 있습니다 (행
                포함/제외·개인정보 확인은 새 파일 기준으로 다시).
              </p>
              <label className="kj-row">
                저장한 작업
                <select
                  ref={jobSelectRef}
                  className="kj-select"
                  aria-label="저장한 작업"
                  value={jobId ?? ''}
                  aria-disabled={jobBusy === 'delete'}
                  onChange={(e) => {
                    if (jobBusy === 'delete') return // 지우는 동안에는 선택을 바꾸지 않는다 (결과·다시 시도는 확인을 연 작업에만)
                    setJobId(e.target.value ? Number(e.target.value) : null)
                    setJobMatch(null)
                    setDelTarget(null)
                  }}
                >
                  {jobs.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.name} (v{j.version} · 입력 {j.input_count}개 · key 열 {j.key_columns}개 · {j.scope_label})
                    </option>
                  ))}
                </select>
              </label>
              <div className="admin-actions kj-actions">
                <button type="button" className="mapping-run" aria-disabled={jobId === null || jobBusy !== null} onClick={() => void matchJob()}>
                  {jobBusy === 'match' ? '맞추는 중…' : '이 파일들에 맞추기'}
                </button>
                {!delTarget && (
                  <button
                    ref={askDeleteRef}
                    type="button"
                    className="login-link"
                    aria-disabled={jobId === null || jobBusy !== null}
                    onClick={openDelete}
                  >
                    이 작업 삭제
                  </button>
                )}
              </div>
              {delTarget && (
                // 모달이 아닌 같은 자리의 확인 — 초점을 가두지 않고, 이 묶음 안에서만 Esc로 닫는다 (다른 입력의 Esc는 그대로)
                <div
                  role="group"
                  aria-labelledby={`${ids}-jobdel`}
                  className="kj-input"
                  onKeyDown={(e) => {
                    if (e.key !== 'Escape' || jobBusy === 'delete') return
                    e.preventDefault()
                    e.stopPropagation()
                    cancelDelete()
                  }}
                >
                  <p id={`${ids}-jobdel`} className="kj-input-title">
                    &lsquo;{delTarget.name}&rsquo; 작업을 지울까요? 실행 기록·결과 파일은 남습니다 (Esc: 취소).
                  </p>
                  <div className="admin-actions kj-actions">
                    {delTarget.unknown ? (
                      <button
                        ref={recheckDeleteRef}
                        type="button"
                        className="mapping-confirm"
                        aria-disabled={jobBusy !== null}
                        onClick={() => void recheckDelete()}
                      >
                        {jobBusy === 'delete' ? '확인 중…' : '삭제 여부 다시 확인'}
                      </button>
                    ) : (
                      <button
                        ref={sureDeleteRef}
                        type="button"
                        className="mapping-confirm"
                        aria-disabled={jobBusy !== null}
                        onClick={() => void removeJob()}
                      >
                        {jobBusy === 'delete' ? '지우는 중…' : '정말 삭제 (실행 기록·결과는 남음)'}
                      </button>
                    )}
                    <button ref={cancelDeleteRef} type="button" className="login-link" aria-disabled={jobBusy === 'delete'} onClick={cancelDelete}>
                      취소
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
          <p role="status" className="mw-muted kj-jobnote">
            {jobNote ?? ''}
          </p>
          {jobsError && (
            <p className="upload-error" role="alert">
              {jobsError}
            </p>
          )}
          {jobMatch && !jobMatch.ready && (
            <div className="kj-input">
              <p className="kj-input-title">
                저장한 작업 &lsquo;{jobMatch.job.name}&rsquo; (v{jobMatch.job.version}) — 입력마다 쓸 파일과 열을 정하세요
              </p>
              {jobMatch.blocked.length > 0 && (
                <ul className="an-problems">
                  {jobMatch.blocked.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              )}
              {jobMatch.roles.map((r) => {
                const saved = jobMatch.job.inputs[r.role - 1]
                const options = r.status === 'assigned' ? r.candidates : files.map((fid) => ({ fid }))
                return (
                  <div key={r.role} className="kj-input">
                    <p className="kj-input-title">
                      입력 {r.role} — 저장 때 &lsquo;{saved?.file_name}&rsquo; / {saved?.sheet.name} ({(saved?.sheet.tab ?? 0) + 1}번째 시트) · key{' '}
                      {saved?.key_columns.map((k) => `${k.letter}열 ${k.name}`).join(' + ')} · 가져올 열 {saved?.columns.length ?? 0}개
                      {saved?.is_base && ' · 기준'}
                    </p>
                    <label className="kj-row">
                      이번 파일
                      <select
                        className="kj-select"
                        aria-label={`입력 ${r.role} 이번 파일`}
                        value={rolePicks[r.role] ?? ''}
                        onChange={(e) => setRolePicks((p) => ({ ...p, [r.role]: e.target.value }))}
                      >
                        <option value="">파일을 고르세요</option>
                        {r.status === 'assigned'
                          ? r.candidates.map((c) => (
                              <option key={`${c.file_id}:${c.sheet_id}`} value={`${c.file_id}:${c.sheet_id}`}>
                                #{c.file_id} {c.file_name} / {c.sheet_name} ({c.tab + 1}번째 시트)
                              </option>
                            ))
                          : options.map((o) =>
                              'file_id' in o ? null : (
                                <option key={o.fid} value={`${o.fid}:${draft.sheets[String(o.fid)] ?? 's0'}`}>
                                  {fileLabel(o.fid)}
                                  {r.candidates.some((c) => c.file_id === o.fid) ? ' — 후보' : ''}
                                </option>
                              ),
                            )}
                      </select>
                    </label>
                    {r.status !== 'assigned' && r.candidates.length > 0 && (
                      <p className="mw-muted">
                        후보 {r.candidates.length}개 (점수 같음): {r.candidates.map((c) => `#${c.file_id} ${c.file_name}`).join(', ')}
                      </p>
                    )}
                    {r.status === 'unassigned' && <p className="mw-muted">key 열이 같은 이름으로 있는 파일이 없습니다 — 파일을 고르면 열을 다시 맞춥니다.</p>}
                    {r.columns
                      .filter((c) => c.status === 'ambiguous' || c.status === 'missing')
                      .map((c) => {
                        const tag = `${c.kind === 'key' ? 'k' : 'p'}${c.position}`
                        const pickKey = `${r.role}:${tag}`
                        return (
                          <label key={pickKey} className="kj-row">
                            {c.kind === 'key' ? `key ${c.position + 1}` : `가져올 열 ${c.position + 1}`} ({c.saved.letter}열 {c.saved.name}) — {c.note}
                            <select
                              className="kj-select"
                              aria-label={`입력 ${r.role} ${c.kind === 'key' ? `key ${c.position + 1}` : `가져올 열 ${c.position + 1}`} 이번 열`}
                              value={colPicks[pickKey] ?? (c.kind === 'pick' && pickKey in colPicks ? '' : '__')}
                              onChange={(e) => setColPicks((p) => ({ ...p, [pickKey]: e.target.value === '__' ? '' : e.target.value }))}
                            >
                              <option value="__">열을 고르세요</option>
                              {c.candidates.map((cand) => (
                                <option key={cand.column_id} value={cand.column_id}>
                                  {cand.letter}열 {cand.name}
                                </option>
                              ))}
                              {(meta[Number((rolePicks[r.role] ?? '').split(':')[0])]?.columns ?? [])
                                .filter((col) => !c.candidates.some((cand) => cand.column_id === col.column_id))
                                .map((col) => (
                                  <option key={col.column_id} value={col.column_id}>
                                    {col.letter}열 {col.name}
                                  </option>
                                ))}
                              {c.kind === 'pick' && <option value="">이번 작업에서 빼기</option>}
                            </select>
                          </label>
                        )
                      })}
                    {r.notes.map((n) => (
                      <p key={n} className="mw-muted">
                        {n}
                      </p>
                    ))}
                  </div>
                )
              })}
              <div className="admin-actions kj-actions">
                <button type="button" className="mapping-run" aria-disabled={jobBusy !== null} onClick={() => void applyPicks()}>
                  {jobBusy === 'apply' ? '적용 중…' : '고른 대로 적용'}
                </button>
              </div>
            </div>
          )}
        </fieldset>
      )}

      {files.length > 0 && applied?.from === 'match' && (
        <Banner
          tone={applied.notes.length + kindNotes.length ? 'warn' : 'info'}
          icon={applied.notes.length + kindNotes.length ? 'alert' : 'info'}
          title={`저장한 작업 '${applied.job.name}' (v${applied.job.version}) 적용 — 변경·예외 ${applied.notes.length + kindNotes.length}건`}
        >
          {applied.notes.length + kindNotes.length === 0 ? (
            <p>저장 때와 같은 구조입니다 — 5단계에서 연결을 확인한 뒤 만드세요 (새 파일의 key·행은 다시 검증합니다).</p>
          ) : (
            <ul>
              {[...applied.notes, ...kindNotes].map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {!fresh && applied.kinds && <p className="mw-muted">key 자료형 변화는 5단계 연결 확인 뒤에 함께 보입니다.</p>}
        </Banner>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend ref={filesLegendRef} tabIndex={-1}>
            1. 파일·시트 ({files.length}개 파일)
          </legend>
          <p className="mw-muted">
            파일마다 연결할 시트 하나가 입력이 됩니다. 같은 이름의 파일은 번호(#)로, 같은 이름의 시트는 순번으로 구분합니다. 빼면 그
            파일의 key·열 설정도 지웁니다.
          </p>
          <ul className="bd-files">
            {files.map((fid) => {
              const m = meta[fid]
              const err = metaError[fid]
              const ref = refs.find((r) => r.fid === fid)
              const vin = ref ? viewInput(ref) : undefined
              return (
                <li key={fid} className={vin?.problems.some((p) => p.level === 'block') ? 'has-problem' : undefined}>
                  <span className="bd-file-name">
                    #{fid} {m?.original_name ?? ''}
                  </span>
                  {m ? (
                    <span className="mw-muted">
                      시트 {m.sheets.length}개 · 열 {m.columns.length}개 · 데이터 {m.data_rows.toLocaleString('ko-KR')}행
                    </span>
                  ) : err ? (
                    <span className="upload-error">
                      시트·열 목록을 읽지 못했습니다: {err}{' '}
                      <button type="button" className="login-link" onClick={() => setMetaVersion((v) => v + 1)}>
                        다시 읽기
                      </button>
                    </span>
                  ) : (
                    <span className="mw-muted">시트·열 목록을 읽는 중…</span>
                  )}
                  {m && m.sheets.length > 1 && (
                    <label className="kj-row">
                      연결할 시트
                      <select
                        className="kj-select"
                        aria-label={`#${fid} ${m.original_name} 연결할 시트`}
                        value={draft.sheets[String(fid)] ?? ''}
                        onChange={(e) => setSheet(fid, e.target.value)}
                      >
                        <option value="">시트를 고르세요</option>
                        {m.sheets.map((s, i) => (
                          <option key={s.sheet_id} value={s.sheet_id}>
                            {s.name} ({i + 1}번째 시트)
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <button
                    type="button"
                    className="login-link"
                    title="연결에서 이 파일을 빼고, 이 파일에 둔 key·가져올 열 설정도 지웁니다"
                    onClick={() => removeFile(fid)}
                  >
                    이 파일 빼기
                  </button>
                  {vin && !vin.own && <span className="upload-tag mapping-unresolved">내 파일 아님 — 조회만 가능</span>}
                  {vin?.pii === 'needs_ack' && vin.pii_summary && (
                    <PiiNotice fileId={fid} pii={vin.pii_summary} onAcknowledged={() => void check(true)} />
                  )}
                </li>
              )
            })}
          </ul>
          <p className="mw-muted">
            파일을 더 고르려면 <NavLink href={`/?files=${filesKey}`}>홈의 파일 목록</NavLink>으로 돌아가세요 (입력 {MIN_INPUTS}~
            {view?.limits.inputs ?? 20}개).
          </p>
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>2. 입력마다 key 열</legend>
          <p className="mw-muted">{KEY_HELP} 복합 key는 여러 열을 순서대로 맞추며, 모든 입력에서 개수·순서가 같아야 합니다.</p>
          {refs.length === 0 && <p className="mw-muted">시트를 정한 입력이 아직 없습니다 (1단계).</p>}
          {refs.map((r, i) => {
            const m = metaOf(r)
            const label = inputLabel(r, i)
            const cols = m?.columns ?? []
            const vin = viewInput(r)
            const compound = Boolean(r.draft.compound) || r.draft.key_columns.length > 1
            const problems = invalid.filter((x) => x.key === r.key && !x.agg) // 집계 항목의 문제는 3단계 집계 구역에서 그 칸에 연결한다
            const noteIds = problems.map((_, n) => `${ids}-fix-${i}-${n}`) // 고칠 것 문구 ↔ key 선택란 (aria-describedby)
            const describedBy = noteIds.length ? noteIds.join(' ') : undefined
            return (
              <div key={r.key} className={`kj-input${problems.length ? ' needs-fix' : ''}`}>
                <p className="kj-input-title">{label}</p>
                <Toggle
                  size="md"
                  label="복합 key (여러 열을 순서대로)"
                  aria-label={`${label} 복합 key (여러 열을 순서대로)`}
                  checked={compound}
                  onChange={(e) => setCompound(r, e.target.checked)}
                />
                {!compound ? (
                  <label className="kj-row">
                    key 열
                    <select
                      className="kj-select"
                      aria-label={`${label} key 열`}
                      aria-invalid={problems.length > 0}
                      aria-describedby={describedBy}
                      value={r.draft.key_columns[0] ?? ''}
                      onChange={(e) => setKeys(r, e.target.value ? [e.target.value] : [])}
                    >
                      <option value="">열 고르기</option>
                      {cols.map((c) => (
                        <option key={c.column_id} value={c.column_id}>
                          {colText(c)}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <>
                    <ol className="kj-list" aria-label={`${label} key 열 순서`}>
                      {r.draft.key_columns.map((cid, n) => (
                        <li key={cid}>
                          <span className="kj-name">
                            key {n + 1}. {colText(cols.find((c) => c.column_id === cid))}
                          </span>
                          <button
                            type="button"
                            className="mapping-confirm"
                            aria-label={`${label} key ${n + 1} 위로`}
                            aria-disabled={n === 0}
                            onClick={() => moveKey(r, n, -1)}
                          >
                            위로
                          </button>
                          <button
                            type="button"
                            className="mapping-confirm"
                            aria-label={`${label} key ${n + 1} 아래로`}
                            aria-disabled={n === r.draft.key_columns.length - 1}
                            onClick={() => moveKey(r, n, 1)}
                          >
                            아래로
                          </button>
                          <button
                            type="button"
                            className="login-link"
                            aria-label={`${label} key ${n + 1} 빼기`}
                            onClick={() => setKeys(r, r.draft.key_columns.filter((x) => x !== cid))}
                          >
                            빼기
                          </button>
                        </li>
                      ))}
                    </ol>
                    {r.draft.key_columns.length < maxKeys && (
                      <label className="kj-row">
                        key 열 추가
                        <select
                          className="kj-select"
                          aria-label={`${label} key 열 추가`}
                          aria-invalid={problems.length > 0}
                          aria-describedby={describedBy}
                          value=""
                          onChange={(e) => e.target.value && setKeys(r, [...r.draft.key_columns, e.target.value])}
                        >
                          <option value="">열 고르기</option>
                          {cols
                            .filter((c) => !r.draft.key_columns.includes(c.column_id))
                            .map((c) => (
                              <option key={c.column_id} value={c.column_id}>
                                {colText(c)}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                  </>
                )}
                {!m && <p className="mw-muted">열 목록을 읽는 중…</p>}
                {vin && vin.key_columns.length > 0 && (
                  <p className="mw-muted">
                    확인 결과{fresh ? '' : ' (직전 확인 기준)'}:{' '}
                    {vin.key_columns.map((k) => `${k.letter}열 ${k.name} — ${kindsText(k.kinds)}`).join(' · ')}
                  </p>
                )}
                {problems.map((p, n) => (
                  <p key={p.text} id={noteIds[n]} className="wk-why" role="note" aria-live="polite">
                    <strong>고칠 것</strong> {p.text}
                  </p>
                ))}
              </div>
            )
          })}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>3. 가져올 열과 결과 이름·순서</legend>
          <p className="mw-muted">
            표준 항목으로 등록되지 않은 열도 고를 수 있습니다. 결과 이름을 비우면 원본 열 이름을 쓰고, 같은 이름이 둘 이상이면 서버가
            뒤의 것에 &lsquo;(입력 n · #파일)&rsquo;을 붙여 구분합니다. 결과 열 순서는 입력 순서 → 아래 순서입니다.
          </p>
          {refs.map((r, i) => {
            const m = metaOf(r)
            const label = inputLabel(r, i)
            const cols = m?.columns ?? []
            // 집계 항목의 고칠 것: 이 구역에 보이고, 문제가 있는 칸에 aria-invalid·aria-describedby로 연결한다
            const aggProblems = r.draft.agg_on ? invalid.filter((x) => x.key === r.key && x.agg) : []
            const aggNoteId = (k: number) => `${ids}-agg-${i}-${k}`
            const aggField = (item: number | null, field: 'func' | 'column' | 'name') => {
              const hits = aggProblems.flatMap((p, k) => (p.agg?.item === item && p.agg?.field === field ? [aggNoteId(k)] : []))
              return hits.length ? { 'aria-invalid': true, 'aria-describedby': hits.join(' ') } : {}
            }
            return (
              <div key={r.key} className="kj-input">
                <p className="kj-input-title">{label}</p>
                <Toggle
                  size="md"
                  label="key별로 집계한 뒤 연결 (같은 key의 여러 행 허용)"
                  aria-label={`${label} key별로 집계한 뒤 연결`}
                  checked={Boolean(r.draft.agg_on)}
                  onChange={(e) => setAggOn(r, e.target.checked)}
                />
                {r.draft.agg_on && (
                  <div className="kj-input">
                    <p className="mw-muted">
                      이 입력은 선택한 규칙으로 key마다 집계한 뒤 연결합니다 — 첫 행을 고르거나 자동으로 합치지 않습니다. 행 수와 값 있는 수는
                      다르며(빈 칸·결측 표기는 세지 않음), 합계·최댓값은 값이 없으면 빈 칸(0 아님)이고 숫자·날짜가 아닌 값이 섞이면 만들지
                      않습니다. 결과 이름을 비우면 &lsquo;열 이름 합계&rsquo;처럼 서버가 붙입니다.
                    </p>
                    <ol className="kj-list" aria-label={`${label} 집계 항목`}>
                      {(r.draft.aggregate ?? []).map((a, n) => {
                        const col = cols.find((x) => x.column_id === a.column_id)
                        return (
                          <li key={n}>
                            <span className="kj-name">{n + 1}.</span>
                            <select
                              className="kj-select"
                              aria-label={`${label} 집계 항목 ${n + 1} 방식`}
                              {...aggField(n, 'func')}
                              value={a.func}
                              onChange={(e) => updateAggItem(r, n, { func: e.target.value as KeyJoinAggFunc, ...(e.target.value === 'count_rows' ? { column_id: null } : {}) })}
                            >
                              {AGG_FUNCS.map((f) => (
                                <option key={f} value={f}>
                                  {KEYJOIN_AGG_LABEL[f]}
                                </option>
                              ))}
                            </select>
                            {a.func !== 'count_rows' && (
                              <select
                                className="kj-select"
                                aria-label={`${label} 집계 항목 ${n + 1} 열`}
                                {...aggField(n, 'column')}
                                value={a.column_id ?? ''}
                                onChange={(e) => updateAggItem(r, n, { column_id: e.target.value || null })}
                              >
                                <option value="">열 고르기</option>
                                {cols
                                  .filter((c) => !r.draft.key_columns.includes(c.column_id))
                                  .map((c) => (
                                    <option key={c.column_id} value={c.column_id}>
                                      {colText(c)}
                                    </option>
                                  ))}
                              </select>
                            )}
                            <input
                              type="text"
                              className="bd-input"
                              aria-label={`${label} 집계 항목 ${n + 1} 결과 이름`}
                              {...aggField(n, 'name')}
                              placeholder={a.func === 'count_rows' ? '행 수' : col ? `${col.name} ${KEYJOIN_AGG_LABEL[a.func]}` : '결과 이름'}
                              maxLength={OUT_NAME_MAX}
                              value={a.out_name_input ?? ''}
                              onChange={(e) => updateAggItem(r, n, { out_name_input: e.target.value })}
                            />
                            <span className="mw-muted">{AGG_HELP[a.func]}</span>
                            <button type="button" className="login-link" aria-label={`${label} 집계 항목 ${n + 1} 빼기`} onClick={() => removeAggItem(r, n)}>
                              빼기
                            </button>
                          </li>
                        )
                      })}
                    </ol>
                    {aggProblems.map((p, k) => (
                      <p key={p.text} id={aggNoteId(k)} className="wk-why" role="note" aria-live="polite">
                        <strong>고칠 것</strong> {p.text}
                      </p>
                    ))}
                    <div className="admin-actions kj-actions">
                      <button
                        type="button"
                        className="mapping-confirm"
                        aria-label={`${label} 집계 항목 추가`}
                        ref={(el) => {
                          aggAddRefs.current[r.key] = el
                        }}
                        aria-describedby={aggField(null, 'func')['aria-describedby']}
                        onClick={() => addAggItem(r)}
                      >
                        집계 항목 추가
                      </button>
                    </div>
                  </div>
                )}
                {!r.draft.agg_on && (
                <div className="kj-cols" role="group" aria-label={`${label} 가져올 열`}>
                  {cols.map((c) => {
                    const isKey = r.draft.key_columns.includes(c.column_id)
                    const on = r.draft.columns.some((x) => x.column_id === c.column_id)
                    return (
                      <label key={c.column_id} className={isKey ? 'mw-muted' : undefined}>
                        <input
                          type="checkbox"
                          aria-label={`${label} ${colText(c)} 결과에 넣기`}
                          checked={!isKey && on}
                          disabled={isKey}
                          onChange={(e) => togglePick(r, c.column_id, e.target.checked)}
                        />
                        <span className="kj-name">
                          {colText(c)}
                          {isKey && ' — key 열은 결과에 이미 들어갑니다'}
                        </span>
                      </label>
                    )
                  })}
                  {!m && <p className="mw-muted">열 목록을 읽는 중…</p>}
                </div>
                )}
                {!r.draft.agg_on && r.draft.columns.length > 0 && (
                  <ol className="kj-list" aria-label={`${label} 결과 열 순서·이름`}>
                    {r.draft.columns.map((c, n) => {
                      const col = cols.find((x) => x.column_id === c.column_id)
                      const name = colText(col)
                      return (
                        <li key={c.column_id}>
                          <span className="kj-name">
                            {n + 1}. {name}
                          </span>
                          <input
                            type="text"
                            className="bd-input"
                            aria-label={`${label} ${name} 결과 이름`}
                            placeholder={col?.name ?? '원본 열 이름'}
                            maxLength={OUT_NAME_MAX}
                            value={c.out_name_input ?? ''}
                            onChange={(e) => setOutName(r, c.column_id, e.target.value)}
                          />
                          <button
                            type="button"
                            className="mapping-confirm"
                            aria-label={`${label} ${name} 위로`}
                            aria-disabled={n === 0}
                            onClick={() => moveColumn(r, n, -1)}
                          >
                            위로
                          </button>
                          <button
                            type="button"
                            className="mapping-confirm"
                            aria-label={`${label} ${name} 아래로`}
                            aria-disabled={n === r.draft.columns.length - 1}
                            onClick={() => moveColumn(r, n, 1)}
                          >
                            아래로
                          </button>
                        </li>
                      )
                    })}
                  </ol>
                )}
              </div>
            )
          })}
          {keyCount > 0 && (
            <div className="kj-input">
              <p className="kj-input-title">key 결과 이름 (비우면 기준 입력의 key 열 이름)</p>
              <ol className="kj-list" aria-label="key 결과 이름">
                {Array.from({ length: keyCount }, (_, n) => (
                  <li key={n}>
                    <label className="kj-row">
                      key {n + 1}
                      <input
                        type="text"
                        className="bd-input"
                        aria-label={`key ${n + 1} 결과 이름`}
                        aria-invalid={keyNameProblems.length > 0}
                        aria-describedby={keyNameIds.length ? keyNameIds.join(' ') : undefined}
                        placeholder={baseMeta?.columns.find((c) => c.column_id === baseRef?.draft.key_columns[n])?.name ?? ''}
                        maxLength={OUT_NAME_MAX}
                        value={draft.key_names_input?.[n] ?? ''}
                        onChange={(e) => setKeyName(n, e.target.value)}
                      />
                    </label>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {keyNameProblems.map((p, n) => (
            <p key={p.text} id={keyNameIds[n]} className="wk-why" role="note" aria-live="polite">
              <strong>고칠 것</strong> {p.text}
            </p>
          ))}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>4. 포함할 key 범위</legend>
          <div className="kj-scope">
            {SCOPES.map((s) => (
              <label key={s}>
                <input type="radio" name={`${ids}-scope`} checked={draft.scope === s} onChange={() => update((d) => ({ ...d, scope: s }))} />
                <span>
                  <strong>{KEYJOIN_SCOPE_LABEL[s]}</strong> <span className="mw-muted">{SCOPE_HELP[s]}</span>
                </span>
              </label>
            ))}
          </div>
          <label className="kj-row">
            기준 파일·시트
            <select
              className="kj-select"
              aria-label="기준 파일·시트"
              value={baseKey ?? ''}
              onChange={(e) => update((d) => ({ ...d, base: e.target.value || null }))}
            >
              {refs.length === 0 && <option value="">시트를 정한 입력이 없음</option>}
              {refs.map((r, i) => (
                <option key={r.key} value={r.key}>
                  {inputLabel(r, i)}
                </option>
              ))}
            </select>
          </label>
          <p className="mw-muted">기준 파일·시트의 행 순서가 결과 순서입니다. 결과 key 칸은 글자 형식이고, 미연결은 빈 칸(0 아님)입니다.</p>
          {view && (
            <p className="mw-muted">
              확인 결과{fresh ? '' : ' (직전 확인 기준)'}: 결과 {view.result.rows.toLocaleString('ko-KR')}행 · 전체 key{' '}
              {view.result.union.toLocaleString('ko-KR')}개 · 공통 key {view.result.intersection.toLocaleString('ko-KR')}개
              {view.scope === 'common' && ` · 선택한 범위에 없어 제외된 key ${view.result.excluded.toLocaleString('ko-KR')}개`}
            </p>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step" aria-busy={busy === 'plan'}>
          <legend>5. 연결 확인 — 결과·문제 미리보기</legend>
          <div className="admin-actions kj-actions">
            <button type="button" className="mapping-run" aria-disabled={!canCheck || busy !== null} onClick={() => void check()}>
              {busy === 'plan' ? '확인 중…' : view ? '다시 연결 확인 (파일은 만들지 않음)' : '연결 확인 — 입력을 다시 읽습니다 (파일은 만들지 않음)'}
            </button>
          </div>
          <p role="status" className="kj-status">
            {statusText}
          </p>
          {!canCheck && busy === null && (
            <p className="wk-why" role="note">
              <strong>누를 수 없음</strong> {invalid[0]?.text ?? `파일이 ${MIN_INPUTS}개 이상 있어야 합니다.`}
            </p>
          )}
          {checkError?.key === reqKey && (
            <div className="upload-error" role="alert">
              <p>연결을 확인하지 못했습니다: {checkError.message}</p>
              <p>설정을 고친 뒤 다시 확인하세요 (새로고침하거나 설정을 지울 필요 없음).</p>
            </div>
          )}
          {view && (
            <div className={fresh ? undefined : 'bd-stale'}>
              {!fresh && (
                <p className="mw-muted bd-checking">설정을 바꿨습니다 — 아래는 직전 확인 결과입니다 (만들기의 근거로 쓰지 않음).</p>
              )}
              <p className="bd-total">
                결과 <strong>{view.result.rows.toLocaleString('ko-KR')}</strong>행 · 열 <strong>{view.result.columns}</strong>개 (key{' '}
                {view.result.key_columns}개 + 가져온 열 {view.result.columns - view.result.key_columns}개) · 합집합{' '}
                {view.result.union.toLocaleString('ko-KR')} · 공통 {view.result.intersection.toLocaleString('ko-KR')} · 빠짐{' '}
                {view.result.excluded.toLocaleString('ko-KR')} · 범위 {KEYJOIN_SCOPE_LABEL[view.scope]}
              </p>
              <p className="mw-muted">
                예산: 결과 칸 {view.budget.output_cells.toLocaleString('ko-KR')} + 인덱스 칸{' '}
                {view.budget.index_cells.toLocaleString('ko-KR')} = {view.budget.cells_weighted.toLocaleString('ko-KR')} /{' '}
                {view.limits.cells.toLocaleString('ko-KR')} · 글자 {view.budget.text_chars.toLocaleString('ko-KR')} /{' '}
                {view.limits.text_chars.toLocaleString('ko-KR')} (출처 시트 포함 — 넘으면 일부만 만들지 않고 아예 만들지 않습니다. 파일·열을
                나눠 만드세요)
              </p>
              <TableRegion label="입력별 확인 결과 표 (가로 스크롤)">
                <table className="upload-table kj-table" aria-label="입력별 확인 결과">
                  <thead>
                    <tr>
                      <th scope="col">입력</th>
                      <th scope="col">key 열</th>
                      <th scope="col">가져올 열 → 결과 이름</th>
                      <th scope="col">데이터 행</th>
                      <th scope="col">key 수</th>
                      <th scope="col">연결</th>
                      <th scope="col">미연결</th>
                      <th scope="col">문제</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.inputs.map((i) => {
                      const blocks = i.problems.filter((p) => p.level === 'block').length
                      const warns = i.problems.length - blocks
                      return (
                        <tr key={`${i.file_id}:${i.sheet_id}`}>
                          <th scope="row">
                            {i.label}
                            {i.index === view.base.index && ' · 기준'}
                            {!i.own && ' · 내 파일 아님'}
                            {i.pii === 'needs_ack' && ' · 개인정보 확인 필요'}
                          </th>
                          <td>{i.key_columns.map((k) => `${k.letter}열 ${k.name}`).join(' + ') || '-'}</td>
                          <td>
                            {i.aggregate
                              ? `집계 후 연결: ${i.aggregate.items.map((a) => `${a.column ? `${a.column.letter}열 ${a.column.name} ` : ''}${a.label} → ${a.out_name}`).join(', ')}`
                              : i.picked.length
                                ? i.picked.map((c) => `${c.letter}열 ${c.name} → ${c.out_name}`).join(', ')
                                : '없음'}
                          </td>
                          <td>
                            {i.rows
                              ? `${i.rows.data.toLocaleString('ko-KR')}${
                                  i.rows.subtotal || i.rows.note ? ` (소계·주석 추정 ${i.rows.subtotal + i.rows.note} — 아래에서 확인)` : ''
                                }`
                              : '-'}
                          </td>
                          <td>
                            {i.keys
                              ? i.aggregate && i.aggregate.source_rows !== null
                                ? `${i.aggregate.source_rows.toLocaleString('ko-KR')}행 → key ${i.keys.indexed.toLocaleString('ko-KR')}개`
                                : i.keys.indexed.toLocaleString('ko-KR')
                              : '분석 전'}
                          </td>
                          <td>{i.keys ? i.keys.linked.toLocaleString('ko-KR') : '-'}</td>
                          <td>{i.keys ? i.keys.unlinked.toLocaleString('ko-KR') : '-'}</td>
                          <td>{i.problems.length ? `차단 ${blocks} · 참고 ${warns}` : '없음'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </TableRegion>
              {view.inputs.some((i) => i.aggregate) && (
                <div className="kj-input">
                  <p className="kj-input-title">집계 검토 정보 — 선택한 규칙으로 집계한 뒤 연결했습니다</p>
                  <p className="mw-muted">
                    집계 전 원본 행·집계 후 key 수는 위 표의 &lsquo;key 수&rsquo; 칸에 있습니다. 검산값은 결과 파일의 그 열을 모두 더한(최댓값은 전체
                    최댓값) 값이라 Excel에서 같은 수식으로 확인하는 기준이 됩니다 — 원본에서 따로 센 값과 비교해야 독립 검산입니다. &lsquo;값 없는
                    key&rsquo;는 그 key에 값이 하나도 없어 빈 칸으로 둔 수(미연결 빈 칸과 구분), &lsquo;0인 key&rsquo;는 실제 0인 수입니다.
                  </p>
                  <TableRegion label="집계 검토 정보 표 (가로 스크롤)">
                    <table className="upload-table kj-table" aria-label="집계 검토 정보">
                      <thead>
                        <tr>
                          <th scope="col">입력</th>
                          <th scope="col">집계 항목 → 결과 이름</th>
                          <th scope="col">검산값</th>
                          <th scope="col">값 없는 key</th>
                          <th scope="col">0인 key</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.inputs.flatMap((i) =>
                          (i.aggregate?.items ?? []).map((a, n) => (
                            <tr key={`${i.file_id}:${i.sheet_id}:${n}`}>
                              <th scope="row">입력 {i.index + 1}</th>
                              <td>
                                {a.column ? `${a.column.letter}열 ${a.column.name} ` : ''}
                                {a.label} → {a.out_name}
                              </td>
                              <td>{a.total === null ? '분석 전' : typeof a.total === 'number' ? a.total.toLocaleString('ko-KR') : a.total}</td>
                              <td>{a.blank_keys === null ? '-' : a.blank_keys.toLocaleString('ko-KR')}</td>
                              <td>{a.zero_keys === null ? '-' : a.zero_keys.toLocaleString('ko-KR')}</td>
                            </tr>
                          )),
                        )}
                      </tbody>
                    </table>
                  </TableRegion>
                </div>
              )}
              {view.inputs.some((i) => i.row_review && i.row_review.total > 0) && (
                <p className="mw-muted">
                  행 수 (입력별 원본 행 · 연결 대상 · 제외 · 미해결):{' '}
                  {view.inputs
                    .map((i) =>
                      i.row_review
                        ? `입력 ${i.index + 1} ${i.row_review.source_rows.toLocaleString('ko-KR')} · ${i.row_review.link_rows.toLocaleString('ko-KR')} · ${i.row_review.excluded} · ${i.row_review.unresolved}`
                        : `입력 ${i.index + 1} -`,
                    )
                    .join(' / ')}{' '}
                  — 결과 {view.result.rows.toLocaleString('ko-KR')}행
                </p>
              )}
              {view.inputs.map((i) => {
                const rr = i.row_review
                if (!rr || rr.total === 0) return null
                const ref = refs.find((r) => r.fid === i.file_id && r.sid === i.sheet_id)
                if (!ref) return null
                return <RowReview key={`${i.file_id}:${i.sheet_id}`} input={i} draftKey={ref.key} decided={draft.rows?.[ref.key]} onDecide={decideRows} />
              })}
              {view.problems.length > 0 && (
                <div className="an-problems" role="alert">
                  <strong>만들 수 없는 이유 {view.problems.length}건</strong>
                  <ProblemList items={view.problems} />
                </div>
              )}
              {view.warnings.length > 0 && (
                <div className="mapping-warn bd-warnings">
                  <strong>참고 {view.warnings.length}건</strong>
                  <ProblemList items={view.warnings} />
                </div>
              )}
              <h3 className="bd-sub">
                연결 결과 미리보기 ({view.preview.shown.toLocaleString('ko-KR')}행 / 전체 {view.preview.total.toLocaleString('ko-KR')}행)
              </h3>
              <p className="mw-muted">{view.preview.note} 값은 가린 표시이며 결과 파일에는 원래 값이 들어갑니다.</p>
              {view.preview.rows.length > 0 ? (
                <TableRegion label="연결 결과 미리보기 표 (가로 스크롤)">
                  <table className="upload-table kj-table kj-preview" aria-label="연결 결과 미리보기">
                    <thead>
                      <tr>
                        {view.header.map((h, n) => (
                          <th key={n} scope="col">
                            {h}
                          </th>
                        ))}
                        <th scope="col">원본 행 (입력 순)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.preview.rows.map((row, n) => (
                        <tr key={n}>
                          {row.key.map((k, j) =>
                            j === 0 ? (
                              <th key={`k${j}`} scope="row">
                                {k}
                              </th>
                            ) : (
                              <td key={`k${j}`} className="kj-key">
                                {k}
                              </td>
                            ),
                          )}
                          {row.cells.map((c, j) => (
                            <td key={j}>{cellText(c)}</td>
                          ))}
                          <td>{view.inputs.map((i) => row.sources.find((s) => s.input === i.index)?.row ?? '—').join(' / ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableRegion>
              ) : (
                <p className="mw-muted">보일 행이 없습니다{view.inputs.some((i) => i.keys === null) ? ' — 위 문제를 해결하면 key를 분석합니다' : ''}.</p>
              )}
            </div>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>6. 설정 확인 · 엑셀 만들기</legend>
          <dl className="kj-summary">
            <dt>입력 {refs.length}개</dt>
            {refs.map((r, i) => {
              const cols = metaOf(r)?.columns ?? []
              const keys = r.draft.key_columns.map((cid) => colText(cols.find((c) => c.column_id === cid))).join(' + ') || '(없음)'
              return (
                <dd key={r.key}>
                  {inputLabel(r, i)}
                  {r.key === baseKey && ' · 기준'} — key {keys} ·{' '}
                  {r.draft.agg_on ? `key별 집계 ${(r.draft.aggregate ?? []).length}항목 (집계한 뒤 연결)` : `가져올 열 ${r.draft.columns.length}개`}
                </dd>
              )
            })}
            <dt>범위</dt>
            <dd>{KEYJOIN_SCOPE_LABEL[draft.scope]}</dd>
            <dt>결과 열 (확인 결과)</dt>
            <dd>{fresh ? fresh.header.join(' · ') : view ? `${view.header.join(' · ')} (직전 확인 기준)` : '연결을 확인하면 보입니다.'}</dd>
          </dl>
          <p className="mw-muted">
            결과 파일: 시트 「연결 결과」(key 열 + 가져온 열) · 「출처」(결과 행마다 입력별 원본 행) · 「연결 요약」. key 열은 글자 형식,
            미연결은 빈 칸(0 아님), 선택하지 않은 열은 넣지 않습니다. &lsquo;계&rsquo;·&lsquo;합계&rsquo;처럼 보이는 값의 행은 연결에서
            빠집니다 — 확인 결과의 행 번호를 확인하세요. 만들기는 확인한 설정(basis)으로만 되고, 바뀌었으면 서버가 거절합니다.
          </p>
          <div className="admin-actions kj-actions">
            <button
              type="button"
              className="mapping-run"
              aria-disabled={why !== null || madeWhy !== null || busy !== null}
              onClick={() => void make(false)}
            >
              {busy === 'run'
                ? '만드는 중…'
                : `엑셀 만들기${fresh ? ` (결과 ${fresh.result.rows.toLocaleString('ko-KR')}행 · 열 ${fresh.result.columns}개)` : ''}`}
            </button>
            {failure?.unknown && pending?.key === reqKey && (
              <button type="button" className="mapping-confirm" aria-disabled={why !== null || busy !== null} onClick={() => void make(true)}>
                같은 요청 다시 보내기 (이미 만들어졌으면 그 실행을 돌려받음)
              </button>
            )}
            {madeFinal && why === null && (
              <button type="button" className="mapping-confirm" aria-disabled={busy !== null} onClick={() => void make(false, true)}>
                같은 설정으로 한 번 더 만들기 (새 실행 — 같은 결과 파일이 하나 더 생김)
              </button>
            )}
          </div>
          {(why ?? madeWhy) && busy === null && (
            <p className="wk-why" role="note">
              <strong>누를 수 없음</strong> {why ?? madeWhy}
            </p>
          )}
          {failure && (
            <div className={failure.unknown ? 'mapping-warn' : 'upload-error'}>
              <p>{failure.message}</p>
              {failure.runId !== null && (
                <p>
                  관련 실행 #{failure.runId} —{' '}
                  <a href="/runs" onClick={toRuns}>
                    실행 기록에서 보기
                  </a>
                </p>
              )}
              {failure.problems.length > 0 && (
                <ul>
                  {failure.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {!failure.unknown && failure.code !== null && (
                <p className="mw-muted">
                  {failure.code === 'KEYJOIN_FAILED' || failure.code === 'KEYJOIN_REQUEST_USED'
                    ? '새 결과 파일은 만들지 않았습니다 — 위 실행은 실패로 기록된 실행입니다. 지금 설정으로 다시 확인했으니 5단계 결과를 보고 다시 만드세요.'
                    : '실행을 새로 만들지 않았습니다. 지금 설정으로 다시 확인했으니 5단계 결과를 보고 다시 만드세요.'}
                </p>
              )}
            </div>
          )}
          <div className={`kj-input${savedHere ? ' is-saved' : ''}`}>
            <p ref={saveTitleRef} tabIndex={-1} className="kj-input-title">
              {savedHere && applied
                ? `저장됨: ${applied.job.name} (v${applied.job.version})`
                : '이 설정을 저장한 작업으로 저장 — 다음 자료에 같은 규칙을 맞춰 씁니다'}
            </p>
            {savedHere && applied ? (
              <p>
                지금 설정이 이 작업에 저장되어 있습니다 — 다시 저장하지 않아도 됩니다. 설정을 바꾸면 &lsquo;갱신&rsquo;으로 이 작업에
                반영하고, 따로 두려면 다른 이름을 적어 새로 저장하세요. 다음 자료는 위 &lsquo;저장한 작업 적용&rsquo;에서 맞춰 씁니다.
              </p>
            ) : (
              applied &&
              savedKey !== null && (
                <p className="mapping-warn">
                  작업 &lsquo;{applied.job.name}&rsquo; (v{applied.job.version})에 저장한 뒤 설정을 바꿨습니다 — 이 작업에 반영하려면
                  &lsquo;갱신&rsquo;을, 따로 두려면 다른 이름으로 저장을 누르세요.
                </p>
              )
            )}
            <p className="mw-muted">
              저장하는 것: 입력마다 시트·열 대응 기준(이름·위치), key 열과 복합 key 순서, 가져올 열·결과 이름·순서, 기준 입력·범위.
              저장하지 않는 것: 파일 자체·행 포함/제외 결정·개인정보 확인·요청 번호·확인 결과. 저장할 때 서버가 연결 확인을 한 번
              거칩니다 (파일은 만들지 않음). 저장한 작업은 본인만 봅니다.
            </p>
            <label className="kj-row">
              작업 이름
              <input
                ref={saveNameRef}
                type="text"
                className="bd-input"
                aria-label="저장할 작업 이름"
                maxLength={100}
                value={saveName}
                onChange={(e) => {
                  setSaveName(e.target.value)
                  if (taken && e.target.value.trim() !== taken.name) setTaken(null) // 다른 이름을 적기 시작하면 앞 이름의 안내는 닫는다
                }}
              />
            </label>
            <div className="admin-actions kj-actions">
              <button type="button" className="mapping-confirm" aria-disabled={!canSave || !saveName.trim()} onClick={() => void saveJob(null)}>
                {jobBusy === 'save' ? '저장 중…' : '저장한 작업으로 저장'}
              </button>
              {/* 같은 자리의 같은 button — 갱신 결과를 모르는 동안 '저장된 판 확인'으로 바뀌어도 누른 단추의 초점이 그대로 남는다 */}
              {applied &&
                (updateUnknown ? (
                  <button type="button" className="mapping-confirm" aria-disabled={jobBusy !== null} onClick={() => void recheckUpdate()}>
                    {jobBusy === 'recheck' ? '확인 중…' : '저장된 판 확인'}
                  </button>
                ) : (
                  // 지금 설정이 이미 저장되어 있으면 누를 수 없음(눌러도 보내지 않고 알림만) — 같은 설정으로 판만 올리지 않게
                  <button type="button" className="mapping-confirm" aria-disabled={!canSave || savedHere} onClick={() => void saveJob(applied)}>
                    작업 &lsquo;{applied.job.name}&rsquo; 갱신 (v{applied.job.version + 1})
                  </button>
                ))}
            </div>
            <p role="status" className="mw-muted kj-jobnote">
              {saveNote ?? ''}
            </p>
            {taken && (
              // 같은 이름(409) 안내 — 저장은 다시 보내지 않았다. 고를 것: 그 작업 고르기 · 지금 설정으로 갱신 · 다른 이름 적기
              <div ref={takenRef} role="group" aria-labelledby={`${ids}-taken`} className="mapping-warn kj-taken">
                <p id={`${ids}-taken`}>
                  {taken.job
                    ? `같은 이름의 작업이 위 '저장한 작업 적용' 목록에 있습니다: '${taken.job.name}' (v${taken.job.version} · ${jobTimeText(taken.job)}). 저장한 작업은 '자동화' 메뉴가 아니라 이 화면에서 봅니다.`
                    : taken.readError !== null
                      ? `같은 이름 '${taken.name}'의 작업이 있다고 서버가 알렸지만 목록을 읽지 못했습니다 (${taken.readError}) — 그 작업은 위 '저장한 작업 적용' 목록에 있습니다.`
                      : `목록을 다시 읽어 보니 '${taken.name}' 작업이 보이지 않습니다 — 그 사이 다른 곳에서 지웠을 수 있습니다. 다시 저장하거나 다른 이름을 적으세요.`}
                </p>
                {takenSaved ? (
                  <p>지금 설정은 이미 이 작업에 저장되어 있습니다 — 다시 저장하지 않아도 됩니다.</p>
                ) : (
                  takenTarget && (
                    <p className="mw-muted">
                      &lsquo;지금 설정으로 갱신&rsquo;은 그 작업에 저장된 설정을 이 화면의 설정으로 바꿉니다 (판 +1 — 이전 판으로 되돌리는 기능은
                      없습니다).
                    </p>
                  )
                )}
                <div className="admin-actions kj-actions">
                  {taken.job && (
                    <button type="button" className="mapping-run" aria-disabled={jobBusy !== null} onClick={pickTaken}>
                      그 작업 고르기
                    </button>
                  )}
                  {takenTarget && !takenSaved && (
                    <button type="button" className="mapping-confirm" aria-disabled={!canSave} onClick={() => void saveJob(takenTarget)}>
                      지금 설정으로 &lsquo;{takenTarget.job.name}&rsquo; 갱신 (v{takenTarget.job.version + 1})
                    </button>
                  )}
                  <button type="button" className="login-link" onClick={focusSaveName}>
                    다른 이름 적기
                  </button>
                </div>
              </div>
            )}
            {!canSave && jobBusy === null && files.length > 0 && (
              <p className="mw-muted">고칠 입력이 없을 때 저장할 수 있습니다 (2·3단계의 &lsquo;고칠 것&rsquo;을 먼저 해결).</p>
            )}
          </div>
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>7. 실행 기록·내려받기</legend>
          {made ? (
            <div className="bd-made">
              <p>
                <strong>
                  {made.run.status !== 'done'
                    ? `실행 #${made.run.id} — ${RUN_STATE[made.run.status] ?? made.run.status}`
                    : made.created
                      ? `실행 #${made.run.id} 완료`
                      : `이미 만든 실행 #${made.run.id}`}
                </strong>
                {made.run.status === 'done' ? (
                  <>
                    {made.run.file_name && ` — ${made.run.file_name}`} · <RunDownloadLink runId={made.run.id}>내려받기</RunDownloadLink>
                  </>
                ) : (
                  ' — 결과가 확정되면 실행 기록에서 내려받을 수 있습니다 (같은 요청 번호로 먼저 시작된 실행)'
                )}{' '}
                ·{' '}
                <a href="/runs" onClick={toRuns}>
                  실행 기록에서 보기
                </a>
              </p>
              <p className="mw-muted">
                결과 {made.run.row_count.toLocaleString('ko-KR')}행 · 시트 순서: {(made.run.summary.sheet_order ?? []).join(' → ') || '-'}
              </p>
              {made.key !== reqKey && <p className="mapping-warn">구성을 바꿨습니다 — 위 결과는 바꾸기 전 구성으로 만든 파일입니다.</p>}
            </div>
          ) : (
            <p className="mw-muted">
              만들면 여기에서 내려받고 실행 기록으로 갈 수 있습니다. 지난 실행은 <NavLink href="/runs">실행 기록</NavLink>에 &lsquo;key
              취합&rsquo;으로 표시됩니다.
            </p>
          )}
        </fieldset>
      )}
    </section>
  )
}

/** '확인이 필요한 행' — 소계·주석으로 추정됐지만 key가 있는 행을 사용자가 포함/제외한다. 위치·근거만 보이고 칸 값은 없다.
 * 화면의 결정(초안)이 서버가 돌려준 결정보다 앞선다(바꾸면 다시 확인). 많은 행은 묶음 선택(이 쪽·모두)과 쪽 보기로 */
function RowReview({
  input,
  draftKey,
  decided,
  onDecide,
}: {
  input: KeyJoinPlanInput
  draftKey: string
  decided: KeyJoinRowDecisions | undefined
  onDecide: (key: string, rows: number[], decision: 'include' | 'exclude' | null) => void
}) {
  const [page, setPage] = useState(0)
  const rr = input.row_review
  if (!rr) return null
  const inc = new Set(decided?.include ?? [])
  const exc = new Set(decided?.exclude ?? [])
  const local = (row: number): 'include' | 'exclude' | null => (inc.has(row) ? 'include' : exc.has(row) ? 'exclude' : null)
  const pages = Math.max(1, Math.ceil(rr.items.length / REVIEW_PAGE))
  const at = Math.min(page, pages - 1)
  const shown = rr.items.slice(at * REVIEW_PAGE, (at + 1) * REVIEW_PAGE)
  const all = rr.items.map((r) => r.row)
  const here = shown.map((r) => r.row)
  const counts = { include: all.filter((r) => inc.has(r)).length, exclude: all.filter((r) => exc.has(r)).length }
  const undecided = all.length - counts.include - counts.exclude + (rr.total - rr.listed)
  const name = `rows-${draftKey.replace(':', '-')}`
  return (
    <section className="kj-review" aria-labelledby={`${name}-h`}>
      <h3 id={`${name}-h`} className="bd-sub">
        {input.label} — 확인이 필요한 행 {rr.total.toLocaleString('ko-KR')}행 (포함 {counts.include} · 제외 {counts.exclude} · 미결정 {undecided})
      </h3>
      <p className="mw-muted">
        소계·주석으로 보이는 값이 있지만 key 칸이 있는 행입니다. 자동으로 빼지 않으니 행마다 포함/제외를 정하세요 (선택하지 않은 열의 값이 근거인
        행도 있습니다 — 판정 칸은 열 문자만 보입니다). 결정을 바꾸면 다시 확인해야 합니다. 포함한 행도 key 중복·빈 값 검사를 그대로 거칩니다.
        {rr.total > rr.listed && ` 목록에는 ${rr.listed.toLocaleString('ko-KR')}행까지만 보입니다 — 나머지는 원본을 나눠 올리세요.`}
      </p>
      <div className="admin-actions kj-actions">
        <button type="button" className="mapping-confirm" onClick={() => onDecide(draftKey, here, 'include')}>
          이 쪽 모두 포함
        </button>
        <button type="button" className="mapping-confirm" onClick={() => onDecide(draftKey, here, 'exclude')}>
          이 쪽 모두 제외
        </button>
        <button type="button" className="mapping-confirm" onClick={() => onDecide(draftKey, all, 'include')}>
          전체 {all.length}행 포함
        </button>
        <button type="button" className="mapping-confirm" onClick={() => onDecide(draftKey, all, 'exclude')}>
          전체 {all.length}행 제외
        </button>
        <button type="button" className="login-link" onClick={() => onDecide(draftKey, all, null)}>
          결정 지우기
        </button>
      </div>
      <TableRegion label={`${input.label} 확인이 필요한 행 (가로 스크롤)`}>
        <table className="upload-table kj-table" aria-label={`${input.label} 확인이 필요한 행`}>
          <thead>
            <tr>
              <th scope="col">원본 행</th>
              <th scope="col">판정 칸</th>
              <th scope="col">근거</th>
              <th scope="col">포함</th>
              <th scope="col">제외</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const d = local(r.row)
              return (
                <tr key={r.row} className={d === null ? 'needs-fix' : undefined}>
                  <th scope="row">{r.row}행</th>
                  <td>{r.column ? `${r.column}열${r.in_key ? ' (key 열)' : ''}` : '—'}</td>
                  <td>{REASON_TEXT[r.reason]}</td>
                  <td>
                    <input type="radio" name={`${name}-${r.row}`} aria-label={`${r.row}행 포함`} checked={d === 'include'} onChange={() => onDecide(draftKey, [r.row], 'include')} />
                  </td>
                  <td>
                    <input type="radio" name={`${name}-${r.row}`} aria-label={`${r.row}행 제외`} checked={d === 'exclude'} onChange={() => onDecide(draftKey, [r.row], 'exclude')} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </TableRegion>
      {pages > 1 && (
        <div className="admin-actions kj-actions" role="group" aria-label={`${input.label} 확인이 필요한 행 쪽 보기`}>
          <button type="button" className="mapping-confirm" aria-disabled={at === 0} onClick={() => setPage(Math.max(0, at - 1))}>
            이전 쪽
          </button>
          <span className="mw-muted">
            {at + 1} / {pages} 쪽 ({REVIEW_PAGE}행씩)
          </span>
          <button type="button" className="mapping-confirm" aria-disabled={at >= pages - 1} onClick={() => setPage(Math.min(pages - 1, at + 1))}>
            다음 쪽
          </button>
        </div>
      )}
    </section>
  )
}

/** 문제·참고 목록 — 서버 문구(text)만 보인다 (위치·개수·코드, key 원문 없음) */
function ProblemList({ items }: { items: KeyJoinProblem[] }) {
  return (
    <ul>
      {items.map((p, i) => (
        <li key={`${p.code}-${i}`}>{p.text}</li>
      ))}
    </ul>
  )
}

export default KeyJoin
