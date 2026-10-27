import { useEffect, useId, useRef, useState } from 'react'
import {
  AnalysisProblemError,
  fetchPreview,
  outcomeText,
  outcomeUnknown,
  planKeyJoin,
  runKeyJoin,
  type Cell,
  type IntegrationRun,
  type KeyJoinInput,
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
import { KEYJOIN_SCOPE_LABEL } from './runFormat'
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
  base: '기준 입력의 key 집합 (기본값) — 다른 입력에 없는 사용자는 그 입력의 열이 빈 칸',
  all: '모든 입력의 합집합 — 기준 입력 순서 뒤에 나머지 입력에서 처음 나온 순서',
  common: '모든 입력에 있는 사용자만 (교집합) — 빠지는 사용자 수는 확인 뒤에 보입니다',
}

// 입력란은 사용자가 친 글자 그대로(…_input) 두고, 서버에는 보낼 수 있는 값만 보낸다 (buildRequest)
type ColumnDraft = { column_id: string; out_name_input?: string }
// compound: 복합 key 편집 모드 (화면 상태 — 요청에는 key_columns의 개수만 남는다)
type InputDraft = { key_columns: string[]; columns: ColumnDraft[]; compound?: boolean }
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
type Invalid = { key: string | null; text: string; about?: 'key_names' }
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
    return {
      file_id: r.fid,
      sheet_id: r.sid,
      key_columns: r.draft.key_columns,
      columns: r.draft.columns
        .filter((c) => !r.draft.key_columns.includes(c.column_id)) // key 열은 결과에 이미 들어간다
        .map((c) => ({ column_id: c.column_id, out_name: nameOf(c.out_name_input) })),
      ...(rows ? { rows } : {}),
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
  const ids = useId()

  const { request, invalid, refs, baseKey } = buildRequest(files, draft, meta, metaError)
  const reqKey = JSON.stringify(request)
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
      <Banner tone="info" icon="info" title="같은 key의 자료(사용자·기관·업무·문서 등)를 정확히 연결합니다 — 유사 일치·공백 제거·대소문자 통일은 하지 않습니다">
        <p>
          입력(파일·시트)마다 key 열을 고르면 서버가 변환 전 값으로 정확히 같은 key만 연결해 key마다 한 행을 만듭니다. 한 입력 안에
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
            const problems = invalid.filter((x) => x.key === r.key)
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
            return (
              <div key={r.key} className="kj-input">
                <p className="kj-input-title">{label}</p>
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
                {r.draft.columns.length > 0 && (
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
          <legend>4. 포함할 사용자 범위</legend>
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
            기준 입력 (파일·시트)
            <select
              className="kj-select"
              aria-label="기준 입력 (파일·시트)"
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
          <p className="mw-muted">기준 입력의 행 순서가 결과 순서입니다. 결과 key 칸은 글자 형식이고, 미연결은 빈 칸(0 아님)입니다.</p>
          {view && (
            <p className="mw-muted">
              확인 결과{fresh ? '' : ' (직전 확인 기준)'}: 합집합 {view.result.union.toLocaleString('ko-KR')}명 · 공통{' '}
              {view.result.intersection.toLocaleString('ko-KR')}명
              {view.scope === 'common' && ` · 빠지는 사용자 ${view.result.excluded.toLocaleString('ko-KR')}명`}
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
                          <td>{i.picked.length ? i.picked.map((c) => `${c.letter}열 ${c.name} → ${c.out_name}`).join(', ') : '없음'}</td>
                          <td>
                            {i.rows
                              ? `${i.rows.data.toLocaleString('ko-KR')}${
                                  i.rows.subtotal || i.rows.note ? ` (소계·주석 추정 ${i.rows.subtotal + i.rows.note} — 아래에서 확인)` : ''
                                }`
                              : '-'}
                          </td>
                          <td>{i.keys ? i.keys.indexed.toLocaleString('ko-KR') : '분석 전'}</td>
                          <td>{i.keys ? i.keys.linked.toLocaleString('ko-KR') : '-'}</td>
                          <td>{i.keys ? i.keys.unlinked.toLocaleString('ko-KR') : '-'}</td>
                          <td>{i.problems.length ? `차단 ${blocks} · 참고 ${warns}` : '없음'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </TableRegion>
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
                  {r.key === baseKey && ' · 기준'} — key {keys} · 가져올 열 {r.draft.columns.length}개
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
