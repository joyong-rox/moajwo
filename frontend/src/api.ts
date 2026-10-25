// 백엔드 API는 항상 /api 경로로 호출한다.
// 개발: Vite proxy, 운영: nginx proxy가 /api 접두어를 떼고 백엔드로 전달한다.
const API_BASE = '/api'

/** 로그인 세션이 끝났을 때(401) 앱이 로그인 화면으로 돌아가도록 알리는 이벤트 */
export const UNAUTHORIZED_EVENT = 'moajwo:unauthorized'

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, { credentials: 'same-origin', ...init })
  if (res.status === 401 && !input.startsWith(`${API_BASE}/auth/`)) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
  }
  return res
}

export type Health = {
  status: string
  database: string
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const res = await apiFetch(`${API_BASE}/health`, { signal })
  // DB 이상 시 백엔드는 503과 함께 상태 본문을 돌려준다.
  if (!res.ok && res.status !== 503) {
    throw new Error(`HTTP ${res.status}`)
  }
  return (await res.json()) as Health
}

export type RowKind = 'data' | 'empty' | 'subtotal' | 'note'
export type Cell = string | number | boolean | null

export type SheetSummary = {
  name: string
  header_row: number | null
  columns: string[]
  data_rows: number
  empty_rows: number
  subtotal_rows: number
  note_rows: number
  missing_cells: number
  header_row_end: number | null
  preview: { row: number; kind: RowKind; cells: Cell[]; missing: number[] }[]
}

export type UploadResult = {
  original_name: string
  id: number | null
  size_bytes: number | null
  encoding: string | null
  sheet_count: number
  data_rows: number
  sheets: SheetSummary[]
  pii: PiiSummary | null
  skipped_sheets?: string[]
  error: string | null
}

export type PiiType = 'rrn' | 'mobile' | 'email'
export const PII_LABEL: Record<PiiType, string> = { rrn: '주민등록번호', mobile: '휴대전화번호', email: '이메일' }
export type PiiItem = { type: PiiType; sheet: string; row: number; column: string; masked: string }
export type PiiSummary = { counts: Record<PiiType, number>; items: PiiItem[]; acknowledged: boolean }

// unanalyzed: 이 사용자의 저장된 분석이 없는 열 (작업 화면에서만, 확정값이 있으면 confirmed)
export type MappingStatus = 'auto' | 'review' | 'unresolved' | 'confirmed' | 'unanalyzed'
export type MappingMethod =
  'exact' | 'standard' | 'learned' | 'exact_part' | 'similarity' | 'pattern' | 'llm' | 'user' | 'none'
export type EngineStatus = 'ok' | 'unavailable' | 'skipped'

export type MappingCandidate = {
  key: string
  label: string
  score: number
  fuzzy: number
  embedding: number | null
}

export type ColumnMapping = {
  column: string // 화면 표시용 (열 이름의 개인정보는 서버가 마스킹)
  column_id: string // 확정 요청에 쓰는 식별자
  samples: Cell[]
  status: MappingStatus
  method: MappingMethod
  field_key: string | null
  field_label: string | null
  score: number | null
  qualifier: string | null
  reason: string | null
  candidates: MappingCandidate[]
}

export type FileMapping = {
  file_id: number
  original_name: string
  embedding: EngineStatus
  llm: EngineStatus
  sheets: { name: string; sheet_id?: string; columns: ColumnMapping[] }[]
  analyzed_at?: string | null
}

export async function mapFile(fileId: number): Promise<FileMapping> {
  const res = await apiFetch(`${API_BASE}/files/${fileId}/mapping`, { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().then(
      (j: { detail?: unknown }) => j.detail,
      () => null,
    )
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return (await res.json()) as FileMapping
}

export type StandardFieldInfo = {
  key: string
  label: string
  description: string
  aliases?: string[] // 표준 사전에 적힌 다른 이름 (사전에 있는 것만)
  modifier?: boolean // 다른 항목의 구분으로 붙는 항목 (관용·자가용 등)
}

export async function fetchFields(): Promise<StandardFieldInfo[]> {
  const res = await apiFetch(`${API_BASE}/mapping/fields`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as StandardFieldInfo[]
}

export type DecisionInput = { sheet: string; column_id: string; field_key: string | null }
export type DecisionsResult = { saved: number; learned: string[]; not_learned: { column: string; reason: string }[] }

export async function saveDecisions(fileId: number, decisions: DecisionInput[]): Promise<DecisionsResult> {
  const res = await apiFetch(`${API_BASE}/files/${fileId}/mapping/decisions`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  if (!res.ok) {
    const detail = await res.json().then(
      (j: { detail?: unknown }) => j.detail,
      () => null,
    )
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return (await res.json()) as DecisionsResult
}

export const ACCEPTED_EXTENSIONS = ['.xlsx', '.xls', '.csv']

/** 업로드. onProgress(0~1)로 실제 전송 진행률을 알린다 (fetch는 업로드 진행률을 주지 않아 XHR 사용). */
export function uploadFiles(files: File[], onProgress?: (fraction: number) => void): Promise<UploadResult[]> {
  const body = new FormData()
  for (const file of files) body.append('files', file, file.name)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/files`)
    xhr.withCredentials = true
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total)
    }
    xhr.upload.onload = () => onProgress?.(1)
    xhr.onerror = () => reject(new Error('네트워크 오류로 업로드하지 못했습니다.'))
    xhr.onload = () => {
      let data: unknown = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        data = null
      }
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as UploadResult[])
        return
      }
      const detail = (data as { detail?: unknown } | null)?.detail
      reject(new Error(typeof detail === 'string' ? detail : `HTTP ${xhr.status}`))
    }
    xhr.send(body)
  })
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().then(
      (j: { detail?: unknown }) => j.detail,
      () => null,
    )
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg ?? '').join(', ')
          : `HTTP ${res.status}`
    throw new Error(message)
  }
  return (await res.json()) as T
}

export type StoredFile = {
  id: number
  original_name: string
  display_name: string // 개인정보를 가린 표시용 이름 (식별은 id)
  uploaded_at: string
  pii: PiiSummary | null
}

export async function listFiles(): Promise<StoredFile[]> {
  return json(await apiFetch(`${API_BASE}/files`))
}

export async function acknowledgePii(fileId: number): Promise<PiiSummary> {
  return json(await apiFetch(`${API_BASE}/files/${fileId}/pii/acknowledge`, { method: 'POST' }))
}

export type ValidationStatus = 'blocked' | 'pii_confirmation_required' | 'passed_with_warnings' | 'passed'
export type ValidationIssue = {
  code: string
  label: string
  message: string
  file_id: number | null
  file_name: string | null
  sheet: string | null
  row: number | null
  column: string | null
}
export type ValidationRun = {
  id: number
  status: ValidationStatus
  blocks: ValidationIssue[]
  warnings: ValidationIssue[]
  pii: {
    file_id: number
    file_name: string
    counts: Record<PiiType, number>
    items: PiiItem[]
    acknowledged: boolean
  }[]
  notes: string[]
  entities: EntityNote[]
  created_at: string
}

export type EntityCandidate = { code: string; name: string; type: string }
export type EntityNote = {
  value: string // 화면 표시용 (개인정보 형식은 마스킹)
  registrable?: boolean // false = 개인정보 형식이 있어 별칭 등록 불가
  field: string
  status: 'unknown' | 'ambiguous' | 'abolished'
  note: string | null
  candidates: EntityCandidate[]
  files: string[]
  count: number
}
export type ValidationRequest = {
  file_ids: number[]
  baseline_file_ids: number[]
  expected_period: string | null
  required_entities: string[]
  entity_field: string | null
  change_threshold: number
}

export async function runValidation(body: ValidationRequest): Promise<ValidationRun> {
  return json(
    await apiFetch(`${API_BASE}/validation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export type GlossaryEntry = {
  id: number
  code: string
  name: string
  type: string | null
  sido_code: string | null
  sigungu_code: string | null
  valid_from: string | null
  valid_to: string | null
  previous_codes: string | null
  note: string | null
  source: 'example' | 'upload' | 'user' | 'base'
  active: boolean
  aliases: string[]
}
export type GlossaryAlias = {
  id: number
  alias: string
  target_code: string
  target_name: string | null
  valid_from: string | null
  valid_to: string | null
  note: string | null
  source: string
}
export type GlossaryInfo = {
  entries: number
  region_entries: number
  example_entries: number
  base_entries: number
  aliases: number
  region_code_table_date: string
}
export type ImportReport = {
  rows: number
  inserted: number
  updated: number
  aliases_added: number
  applied: boolean
  errors: { row: number | null; message: string }[]
  warnings: { row: number | null; message: string }[]
}
export type AliasInput = {
  alias: string
  target_code: string
  valid_from?: string | null
  valid_to?: string | null
  note?: string | null
}

export const GLOSSARY_TEMPLATE_URL = `${API_BASE}/glossary/template.csv`
export const GLOSSARY_EXPORT_URL = `${API_BASE}/glossary/export.csv`

export async function glossaryInfo(): Promise<GlossaryInfo> {
  return json(await apiFetch(`${API_BASE}/glossary/info`))
}

export async function listEntries(q: string, scope: 'all' | 'org' | 'region' = 'all'): Promise<GlossaryEntry[]> {
  const params = new URLSearchParams({ scope, limit: '100' })
  if (q.trim()) params.set('q', q.trim())
  return json(await apiFetch(`${API_BASE}/glossary/entries?${params}`))
}

export async function deleteEntry(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/glossary/entries/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export async function importGlossary(file: File): Promise<ImportReport> {
  const body = new FormData()
  body.append('file', file, file.name)
  return json(await apiFetch(`${API_BASE}/glossary/import`, { method: 'POST', body }))
}

export async function listAliases(): Promise<GlossaryAlias[]> {
  return json(await apiFetch(`${API_BASE}/glossary/aliases`))
}

export async function createAlias(body: AliasInput): Promise<GlossaryAlias> {
  return json(
    await apiFetch(`${API_BASE}/glossary/aliases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteAlias(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/glossary/aliases/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export type ReviewCategory = 'block' | 'pending' | 'auto' | 'warning'
export type ReviewItem = {
  id: number
  category: ReviewCategory
  kind: string
  file_id: number | null
  file_name: string | null
  sheet: string | null
  row: number | null
  column: string | null
  before: string | null
  after: string | null
  reason: string
  reason_source: 'rule' | 'llm'
  payload: {
    candidates?: { key?: string; code?: string; label?: string; name?: string; score?: number }[]
    field_key?: string | null
    count?: number
    [key: string]: unknown
  }
  decision: 'approved' | 'rejected' | null
  choice: string | null
  excluded: boolean
  can_change?: boolean // false = 남이 올린 파일이라 조회 전용 (승인·거부 불가)
}
export type Review = {
  id: number
  status: 'in_review' | 'ready' | 'superseded'
  score: number
  summary: string
  counts: { block: number; pending: number; pending_open: number; auto: number; warning: number }
  can_complete: boolean
  remember_format: boolean
  files: { id: number; name: string; excluded: boolean }[]
  items: ReviewItem[]
  validation_run_id: number | null
  learned: { columns: number; names: number; names_skipped?: number } | null
  parent_id: number | null
  child_id: number | null
  latest_id?: number | null // 대체된 검토면 다시 검사로 이어진 최신 검토
  history: { id: number; status: string; score: number; blocks: number; pending_open: number }[]
  changes: ReviewChanges | null
  automation_id: number | null
  automation_name: string | null
  created_by: number | null
  created_by_name: string | null
  can_edit: boolean
  include_unmapped_default: boolean
  needs_recheck?: boolean // 검사에 적용한 열 매핑(확정·자동 적용)과 지금이 달라 이 검사 결과로 완료·통합할 수 없음
  recheck_reasons?: string[] // 다른 점 (열마다 '검사 때 → 지금', 이전 버전 검토는 확인할 수 없다는 안내)
}
export type ReviewChangeItem = {
  category: ReviewCategory
  kind: string
  before: string | null
  after: string | null
  file_name: string | null
  column: string | null
}
export type ReviewChanges = {
  parent_id: number
  score: [number, number]
  blocks: [number, number]
  pending_open: [number, number]
  replaced_files: Record<string, number>
  resolved: ReviewChangeItem[]
  added: ReviewChangeItem[]
}

export async function createReview(body: ValidationRequest): Promise<Review> {
  return json(
    await apiFetch(`${API_BASE}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function decideReviewItem(
  reviewId: number,
  itemId: number,
  decision: 'approved' | 'rejected',
  choice: string | null,
): Promise<Review> {
  return json(
    await apiFetch(`${API_BASE}/reviews/${reviewId}/items/${itemId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, choice }),
    }),
  )
}

export async function setRememberFormat(reviewId: number, remember: boolean): Promise<Review> {
  return json(
    await apiFetch(`${API_BASE}/reviews/${reviewId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remember_format: remember }),
    }),
  )
}

export async function completeReview(reviewId: number): Promise<Review> {
  return json(await apiFetch(`${API_BASE}/reviews/${reviewId}/complete`, { method: 'POST' }))
}

export async function recheckReview(reviewId: number, replace: Record<number, number>): Promise<Review> {
  return json(
    await apiFetch(`${API_BASE}/reviews/${reviewId}/recheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ replace }),
    }),
  )
}

export type IntegrationRun = {
  id: number
  review_session_id: number | null
  status: 'done' | 'failed' | 'running' | 'review'
  status_label: string
  trigger: 'manual' | 'automation'
  automation_id: number | null
  automation_name: string | null
  user_id: number | null
  user_name: string | null
  input_file_count: number
  output_path: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  file_name: string | null
  row_count: number
  change_count: number
  warning_count: number
  summary: {
    processed_at?: string
    cell_change_count?: number
    column_change_count?: number
    change_counts?: Record<string, number>
    columns?: string[]
    include_unmapped?: boolean
    unmapped_columns?: string[]
    mode?: 'sheets' // 파일별 시트로 묶기 (없으면 하나의 통합표)
    stats_summary?: boolean
    sheet_order?: string[]
  }
  error: string | null
  created_at: string
}

export const runDownloadUrl = (runId: number) => `${API_BASE}/runs/${runId}/download`

const XLSX_TYPE = 'spreadsheetml'

/** 실행 결과 내려받기 결과. 실패하면 화면에 보일 이유 (status 0 = 서버에 닿지 못함·취소함) */
export type DownloadOutcome = { ok: true } | { ok: false; status: number; message: string }

const DOWNLOAD_CANCELLED: DownloadOutcome = { ok: false, status: 0, message: '내려받기를 취소했습니다.' }

/** 서버의 410·401·403·404·409·5xx를 구분해 사용자가 할 수 있는 일을 알린다 (서버 문구가 있으면 그것을 쓴다). */
function downloadProblem(status: number, detail: string | null): string {
  if (status === 410) {
    return (
      '기록은 남아 있으나 결과 파일을 찾을 수 없습니다 — 서버 재배포·재시작으로 지워졌을 수 있습니다. ' +
      '실행 기록·변경 내역은 그대로입니다. 새 결과가 필요하면 원본을 다시 올려 다시 실행하세요.'
    )
  }
  if (status === 401) return '로그인이 끝났습니다 — 다시 로그인한 뒤 내려받으세요.'
  if (status === 403) return '이 결과를 내려받을 권한이 없습니다.'
  if (status === 404) return '결과를 찾을 수 없습니다 — 지워졌거나 볼 수 없는 실행입니다.'
  if (status === 409) return detail ?? '아직 결과 파일이 없는 실행입니다.'
  if (status >= 500) return detail ?? '서버 오류로 내려받지 못했습니다 — 잠시 뒤 다시 시도하세요.'
  return detail ?? `내려받지 못했습니다 (HTTP ${status}).`
}

/** Content-Disposition의 파일 이름 (filename*=UTF-8''… 우선) */
function attachmentName(header: string | null): string | null {
  if (!header) return null
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // 잘못 인코딩된 이름은 아래의 일반 이름을 쓴다
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1] : null
}

function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.hidden = true
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** 결과 파일을 앱 안에서 받아 저장한다. 성공한 Excel 응답을 끝까지 받았을 때만 저장하고, 오류 응답(JSON)·도중에 끊긴
 *  본문·취소(signal — 링크가 화면에서 내려감·로그아웃)는 파일로 저장하지 않는다. 다시 받기는 사용자가 누를 때만 한다.
 *  화면은 그대로 두므로 목록·검색 조건·키보드 초점이 남는다. 401이면 apiFetch가 로그인 화면으로 돌아가게 알린다. */
export async function downloadRunResult(runId: number, signal?: AbortSignal): Promise<DownloadOutcome> {
  let res: Response
  try {
    res = await apiFetch(runDownloadUrl(runId), { signal })
  } catch {
    if (signal?.aborted) return DOWNLOAD_CANCELLED
    return { ok: false, status: 0, message: '서버에 연결하지 못했습니다 — 연결을 확인한 뒤 다시 내려받으세요.' }
  }
  if (res.ok && (res.headers.get('content-type') ?? '').includes(XLSX_TYPE)) {
    let blob: Blob
    try {
      blob = await res.blob() // 헤더 뒤 본문이 끊기면(연결 끊김·길이 불일치) 여기서 실패한다 — 받은 일부는 저장하지 않는다
    } catch {
      if (signal?.aborted) return DOWNLOAD_CANCELLED
      return { ok: false, status: res.status, message: '파일을 받는 도중 연결이 끊겨 저장하지 않았습니다 — 다시 내려받으세요.' }
    }
    // 본문을 다 받은 뒤의 취소는 blob()을 실패시키지 않는다 — 그 사이 화면이 내려갔으면 저장하지 않는다
    if (signal?.aborted) return DOWNLOAD_CANCELLED
    saveBlob(blob, attachmentName(res.headers.get('content-disposition')) ?? `결과-${runId}.xlsx`)
    return { ok: true }
  }
  if (res.ok) return { ok: false, status: res.status, message: '결과 파일 형식이 아닌 응답이라 저장하지 않았습니다.' }
  const detail = await res.json().then(
    (j: { detail?: unknown } | null) => (typeof j?.detail === 'string' ? j.detail : null),
    () => null,
  )
  return { ok: false, status: res.status, message: downloadProblem(res.status, detail) }
}

/** includeUnmapped: 표준 항목이 없는('해당 없음') 열도 원래 이름으로 결과에 넣을지 */
export async function integrateReview(reviewId: number, includeUnmapped?: boolean): Promise<IntegrationRun> {
  return json(
    await apiFetch(`${API_BASE}/reviews/${reviewId}/integrate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ include_unmapped: includeUnmapped ?? null }),
    }),
  )
}

export async function listRuns(reviewId: number): Promise<IntegrationRun[]> {
  return json(await apiFetch(`${API_BASE}/runs?review_id=${reviewId}`))
}

export async function getReview(reviewId: number): Promise<Review> {
  return json(await apiFetch(`${API_BASE}/reviews/${reviewId}`))
}

/** 검토 목록 항목 (조회 전용). 볼 수 있는 범위는 단건 조회와 같다 (일반 사용자는 자기 검토, 최고 관리자는 전체). */
export type ReviewListItem = {
  id: number
  status: 'in_review' | 'ready' | 'superseded'
  status_label: string
  needs_recheck: boolean
  file_count: number
  file_names: string[] // 앞의 몇 개, 개인정보 형식은 가림
  created_at: string
  completed_at: string | null
  created_by: number | null
  created_by_name: string | null
  automation_name: string | null
  latest_id: number
  run_count: number
  last_run_id: number | null
  can_edit: boolean
}

export type ReviewFilter = 'all' | 'open' | 'recheck' | 'ready'
// next_before: 이어서 볼 때 넘길 번호 — null이면 조건에 맞는 검토를 끝까지 살펴봄 ('다시 검사 필요'는 items가 비어도 남을 수 있음)
export type ReviewPage = { items: ReviewListItem[]; next_before: number | null }
// 볼 수 있는 검토 전체의 상태별 건수 (목록에 불러온 범위와 상관없음)
export type ReviewCounts = { total: number; live: number; open: number; ready: number; superseded: number; recheck: number }

/** 상태 조건을 서버에서 먼저 걸고 최신 순으로 나눠 받는다 (before = 앞 쪽의 next_before). */
export async function listReviews(
  filter: ReviewFilter,
  includeSuperseded: boolean,
  before: number | null = null,
  limit = 50,
): Promise<ReviewPage> {
  const q = new URLSearchParams({ filter, limit: String(limit) })
  if (includeSuperseded) q.set('include_superseded', 'true')
  if (before !== null) q.set('before', String(before))
  return json(await apiFetch(`${API_BASE}/reviews?${q.toString()}`))
}

export async function countReviews(): Promise<ReviewCounts> {
  return json(await apiFetch(`${API_BASE}/reviews/counts`))
}

export async function listAllRuns(limit = 100): Promise<IntegrationRun[]> {
  return json(await apiFetch(`${API_BASE}/runs?limit=${limit}`))
}

export type ColumnRule = { key: string; column: string; field_key: string | null; field_label: string | null }
export type Automation = {
  id: number
  name: string
  owner_id: number | null
  owner_name: string | null
  manual_baseline_minutes: number | null
  source_run_id: number | null
  column_rules: ColumnRule[]
  entity_rules: Record<string, string | null>
  validation: {
    required_entities?: string[] | null
    entity_field?: string | null
    change_threshold?: number | null
    expected_period?: string | null
  }
  output_format: { columns?: string[] }
  created_at: string
  updated_at: string | null
  run_count: number
  last_run: IntegrationRun | null
}
export type AutomationRunResult = {
  run: IntegrationRun
  review_id: number
  counts: { block: number; pending: number; pending_open: number; auto: number; warning: number }
  summary: string
}

export async function saveAutomation(runId: number, name: string): Promise<Automation> {
  return json(
    await apiFetch(`${API_BASE}/runs/${runId}/automation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  )
}

export async function listAutomations(): Promise<Automation[]> {
  return json(await apiFetch(`${API_BASE}/automations`))
}

export async function deleteAutomation(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/automations/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export async function runAutomation(
  id: number,
  body: { file_ids: number[]; baseline_file_ids: number[]; expected_period: string | null },
): Promise<AutomationRunResult> {
  return json(
    await apiFetch(`${API_BASE}/automations/${id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

// ---------------------------------------------------------------- 로그인·계정

export type Role = 'user' | 'owner'
export const ROLE_LABEL: Record<Role, string> = { user: '일반 사용자', owner: '최고 관리자(소유자)' }
export type Me = { id: number; username: string; display_name: string; role: Role; role_label: string }

const jsonBody = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export async function authStatus(): Promise<{ setup_required: boolean; user: Me | null }> {
  return json(await apiFetch(`${API_BASE}/auth/status`))
}

/** 로그인돼 있으면 사용자, 아니면 null */
export async function fetchMe(): Promise<Me | null> {
  const res = await apiFetch(`${API_BASE}/auth/me`)
  if (res.status === 401) return null
  return json(res)
}

/** 승인 대기·반려·이용 중지 계정은 비밀번호가 맞을 때만 403과 함께 상태 안내가 온다 (오류 메시지로 보여준다). */
export async function login(username: string, password: string): Promise<Me> {
  return json(await apiFetch(`${API_BASE}/auth/login`, { method: 'POST', ...jsonBody({ username, password }) }))
}

export async function setupOwner(body: { username: string; display_name: string; password: string }): Promise<Me> {
  return json(await apiFetch(`${API_BASE}/auth/setup`, { method: 'POST', ...jsonBody(body) }))
}

export type SignupResult = { username: string; status: AccountStatus; status_label: string; message: string }

/** 가입 신청 (역할·상태는 서버가 정한다 — 일반 사용자·승인 대기). 세션은 생기지 않는다. */
export async function signup(body: {
  username: string
  display_name: string
  password: string
  password_confirm: string
}): Promise<SignupResult> {
  return json(await apiFetch(`${API_BASE}/auth/signup`, { method: 'POST', ...jsonBody(body) }))
}

export async function logout(): Promise<void> {
  await apiFetch(`${API_BASE}/auth/logout`, { method: 'POST' })
}

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/auth/password`, {
    method: 'POST',
    ...jsonBody({ current_password, new_password }),
  })
  if (!res.ok) await json(res)
}

export type AccountStatus = 'pending' | 'active' | 'rejected' | 'suspended'
export const ACCOUNT_STATUS_LABEL: Record<AccountStatus, string> = {
  pending: '승인 대기',
  active: '이용 중',
  rejected: '가입 반려',
  suspended: '이용 중지',
}

export type UserAccount = {
  id: number
  username: string
  display_name: string
  role: Role
  role_label: string
  status: AccountStatus
  status_label: string
  status_reason: string | null
  status_changed_at: string | null
  is_active: boolean
  created_at: string
  last_login_at: string | null
  last_activity_at: string | null
}

export async function listUsers(params: { q?: string; status?: AccountStatus } = {}): Promise<UserAccount[]> {
  const query = new URLSearchParams()
  if (params.q) query.set('q', params.q)
  if (params.status) query.set('status', params.status)
  const qs = query.toString()
  return json(await apiFetch(`${API_BASE}/users${qs ? `?${qs}` : ''}`))
}

/** 최고 관리자가 직접 추가하는 계정 — 항상 일반 사용자, 승인 여부(status)를 명시한다. */
export async function createUser(body: {
  username: string
  display_name: string
  status: 'active' | 'pending'
  password: string
}): Promise<UserAccount> {
  return json(await apiFetch(`${API_BASE}/users`, { method: 'POST', ...jsonBody(body) }))
}

export type StatusAction = 'approve' | 'reject' | 'suspend' | 'resume'

export async function changeUserStatus(id: number, action: StatusAction, reason: string | null): Promise<UserAccount> {
  return json(await apiFetch(`${API_BASE}/users/${id}/${action}`, { method: 'POST', ...jsonBody({ reason }) }))
}

export async function resetPassword(id: number, password: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/users/${id}/password`, { method: 'POST', ...jsonBody({ password }) })
  if (!res.ok) await json(res)
}

// ---------------------------------------------------------------- 관리 화면 (/moajwoAdmin)

export type PeriodCount = { total: number; inactive_accounts: number; no_owner: number }

export type AdminOverview = {
  start: string
  end: string
  timezone: string
  generated_at: string
  users: { status: AccountStatus; label: string; count: number }[]
  owners: number
  uploads: PeriodCount
  runs: PeriodCount & { done: number; failed: number; review: number; running: number }
}

export async function fetchAdminOverview(start: string, end: string): Promise<AdminOverview> {
  const qs = new URLSearchParams({ start, end }).toString()
  return json(await apiFetch(`${API_BASE}/admin/overview?${qs}`))
}

export type AuditEvent = {
  id: number
  created_at: string
  actor_name: string | null
  target_id: number | null
  target_name: string
  action: string
  action_label: string
  before_status: string | null
  after_status: string | null
  before_label: string | null
  after_label: string | null
  role_change: string | null
  reason: string | null
  result: 'ok' | 'denied'
  result_label: string
}

export async function fetchAudit(limit = 200): Promise<AuditEvent[]> {
  return json(await apiFetch(`${API_BASE}/admin/audit?limit=${limit}`))
}

// ---------------------------------------------------------------- 대시보드·승격·설정

export type Dashboard = {
  scope: 'mine' | 'all'
  month: string
  month_runs: number
  month_done: number
  saved_seconds: number
  saved_runs: number
  runs_without_baseline: number
  baseline_minutes: number | null
  validated_rows: number
  validated_files: number
  errors_found: number
  blocks_found: number
  warnings_found: number
}

export async function fetchDashboard(): Promise<Dashboard> {
  return json(await apiFetch(`${API_BASE}/dashboard`))
}

export type PromotionCandidate = {
  id: number
  column_name: string
  normalized: string
  field_key: string
  field_label: string | null
  approver_count: number
  approvers: string[]
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  reviewed_by_name: string | null
  reviewed_at: string | null
  review_note: string | null
}

export async function listPromotions(
  status: 'pending' | 'approved' | 'rejected' | 'all',
): Promise<{ threshold: number; items: PromotionCandidate[] }> {
  return json(await apiFetch(`${API_BASE}/admin/promotions?status=${status}`))
}

export async function decidePromotion(
  id: number,
  decision: 'approve' | 'reject',
  note: string | null,
): Promise<PromotionCandidate> {
  return json(
    await apiFetch(`${API_BASE}/admin/promotions/${id}/${decision}`, { method: 'POST', ...jsonBody({ note }) }),
  )
}

export type StandardTerm = {
  id: number
  term: string
  normalized: string
  field_key: string
  field_label: string | null
  approved_by_name: string | null
  created_at: string
}

export async function listStandardTerms(): Promise<StandardTerm[]> {
  return json(await apiFetch(`${API_BASE}/admin/standard-terms`))
}

export async function deleteStandardTerm(id: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/admin/standard-terms/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export type Settings = {
  manual_baseline_minutes: number | null
  automations: { id: number; name: string; owner_name: string | null; manual_baseline_minutes: number | null }[]
  updated_at: string | null
  updated_by_name: string | null
}

export async function fetchSettings(): Promise<Settings> {
  return json(await apiFetch(`${API_BASE}/settings`))
}

export async function saveBaseline(minutes: number | null): Promise<Settings> {
  return json(
    await apiFetch(`${API_BASE}/settings`, { method: 'PUT', ...jsonBody({ manual_baseline_minutes: minutes }) }),
  )
}

export async function saveAutomationBaseline(id: number, minutes: number | null): Promise<Settings> {
  return json(
    await apiFetch(`${API_BASE}/settings/automations/${id}`, {
      method: 'PUT',
      ...jsonBody({ manual_baseline_minutes: minutes }),
    }),
  )
}

// ---------------------------------------------------------------- LLM 제공자 (소유자)

export type LlmMode = 'ollama' | 'openai' | 'anthropic' | 'custom'
export type LlmSettings = {
  mode: LlmMode
  mode_label: string
  model: string | null
  effective_model: string | null
  base_url: string | null
  effective_base_url: string
  api_key_set: boolean
  api_key_hint: string | null
  is_external: boolean
  modes: Record<LlmMode, string>
  default_models: Partial<Record<LlmMode, string>>
  embedding: string
  updated_at: string | null
  updated_by_name: string | null
}

export async function fetchLlmSettings(): Promise<LlmSettings> {
  return json(await apiFetch(`${API_BASE}/settings/llm`))
}

/** api_key: undefined = 저장된 키 유지, '' = 삭제, 값 = 새 키 */
export async function saveLlmSettings(body: {
  mode: LlmMode
  model: string | null
  base_url: string | null
  api_key?: string
}): Promise<LlmSettings> {
  return json(await apiFetch(`${API_BASE}/settings/llm`, { method: 'PUT', ...jsonBody(body) }))
}

export async function testLlmSettings(): Promise<{
  ok: boolean
  mode_label: string
  model: string | null
  latency_ms: number
  message: string
}> {
  return json(await apiFetch(`${API_BASE}/settings/llm/test`, { method: 'POST' }))
}

// ---------------------------------------------------------------- 열 매핑 작업 화면 (미리보기·저장된 분석·공통 검토·일괄 확정)

/** 작업 화면 매핑: 이 사용자가 마지막으로 실행한 분석(있으면) + 지금의 확정값.
 * 분석 기록이 없으면 analyzed=false — 확정값만 보이고 나머지 열은 'unanalyzed' (자동으로 분석하지 않는다). */
export type StoredMapping = {
  file_id: number
  original_name: string
  analyzed: boolean
  analyzed_at: string | null
  embedding: EngineStatus | null
  llm: EngineStatus | null
  stale: boolean
  can_change: boolean
  sheets: { sheet_id: string; name: string; columns: ColumnMapping[] }[]
}

export async function fetchLatestMapping(fileId: number, signal?: AbortSignal): Promise<StoredMapping> {
  return json(await apiFetch(`${API_BASE}/files/${fileId}/mapping/latest`, { signal }))
}

export type PreviewColumn = { column_id: string; name: string; letter: string }
export type PreviewRow = { row: number; kind: RowKind; cells: Cell[] }
export type SheetPreview = {
  file_id: number
  original_name: string
  sheet_id: string
  sheet: string
  sheets: { sheet_id: string; name: string }[]
  basis: string
  header_row: number | null
  header_row_end: number | null
  header_rows: Cell[][]
  columns: PreviewColumn[]
  rows: PreviewRow[]
  offset: number
  limit: number
  total_rows: number
  data_rows: number
  dropped_columns: number
}

export async function fetchPreview(
  fileId: number,
  sheetId: string | null,
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<SheetPreview> {
  const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (sheetId) qs.set('sheet_id', sheetId)
  return json(await apiFetch(`${API_BASE}/files/${fileId}/preview?${qs.toString()}`, { signal }))
}

export type CommonCategory = 'common' | 'divergent' | 'unresolved' | 'only'
export type CommonMember = {
  file_id: number
  file_name: string
  sheet_id: string
  sheet: string
  column_id: string
  column: string
  samples: Cell[]
  status: MappingStatus
  method: MappingMethod
  field_key: string | null
  field_label: string | null
  qualifier: string | null
  reason: string | null // 제안 근거 (서버가 가림)
  confirmed: boolean
  value_kind: string
  quantity: boolean // 수량으로 비교하는 열 (단위·흐름/시점 비교 대상)
  unit: string | null
  unit_state: 'one' | 'none' | 'ambiguous'
  unit_note: string | null
  basis: 'flow' | 'stock' | 'conflict' | null
  can_change: boolean
}
export type CommonGroup = {
  group_id: string
  category: CommonCategory
  name: string
  files: number
  field_key: string | null
  field_label: string | null
  reasons: string[]
  evidence: string[] // 공통 후보에서 실제로 확인한 근거 (확인하지 못한 것은 그렇다고 적음)
  members: CommonMember[]
}
export type CommonReview = {
  files: { file_id: number; name: string; analyzed_at: string; stale: boolean; can_change: boolean }[]
  missing: { file_id: number; name: string }[]
  groups: CommonGroup[]
}

export async function fetchCommonReview(fileIds: number[]): Promise<CommonReview> {
  return json(
    await apiFetch(`${API_BASE}/mapping/common`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    }),
  )
}

export type BatchTarget = { file_id: number; sheet_id: string; column_id: string; field_key: string | null }
export type PlanItem = {
  file_id: number
  file_name: string
  sheet_id: string
  sheet: string
  column_id: string
  column: string
  current_status: MappingStatus | 'unknown'
  suggestion_reason: string | null
  current_confirmed: boolean
  current_field_key: string | null
  current_label: string | null
  new_field_key: string | null
  new_label: string | null
  action: 'apply' | 'overwrite' | 'unchanged' | 'excluded'
  reason: string | null
}
export type BatchPlan = {
  selected: number
  applicable: number
  overwrite: number
  unchanged: number
  excluded: number
  items: PlanItem[]
}

export async function previewBatch(items: BatchTarget[]): Promise<BatchPlan> {
  return json(
    await apiFetch(`${API_BASE}/mapping/batch/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    }),
  )
}

export type ApplyTarget = BatchTarget & {
  expected_confirmed: boolean
  expected_field_key: string | null
  overwrite: boolean
}

/** 적용하지 못한 이유 목록을 담은 오류 (409: 확인 뒤 확정값 변경·덮어쓰기 미선택·적용 불가 대상). */
export class BatchConflictError extends Error {
  problems: string[]
  constructor(message: string, problems: string[]) {
    super(message)
    this.problems = problems
  }
}

export async function applyBatch(
  items: ApplyTarget[],
): Promise<{ applied: number; unchanged: number; learned: number; files: number }> {
  const res = await apiFetch(`${API_BASE}/mapping/batch/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
  if (res.status === 409) {
    const detail = await res.json().then(
      (j: { detail?: { message?: string; problems?: string[] } }) => j.detail,
      () => undefined,
    )
    throw new BatchConflictError(detail?.message ?? '적용하지 않았습니다.', detail?.problems ?? [])
  }
  return json(res)
}

// ---------------------------------------------------------------- 데이터 분석 (확정된 열로 합계·평균 등)

export type AggKey = 'sum' | 'mean' | 'min' | 'max' | 'count'
export type AnalysisField = { key: string; label: string; files: number; sheets: number }
export type AnalysisSource = {
  file_id: number
  file_name: string
  uploaded_at: string | null
  sheets: string[]
  columns: number
}
export type AnalysisTargetIn = {
  file_id: number
  sheet_id?: string | null
  column_id?: string | null
  unit?: string | null
  period?: string | null
  rows_are_records?: boolean // 같은 대상의 행이 반복될 때 '서로 다른 기록'이라고 확인함
}
export type AnalysisBin = {
  label?: string | null
  min: number | null
  max: number | null
  min_inclusive: boolean
  max_inclusive: boolean
}
export type AnalysisFilters = {
  dims: Record<string, string[]>
  period_from: string | null
  period_to: string | null
  value_min: number | null
  value_max: number | null
}
export type AnalysisRequest = {
  field_key: string
  variant?: string | null
  unit?: string | null
  targets: AnalysisTargetIn[]
  group_by: string[]
  filters: AnalysisFilters
  aggregates: AggKey[]
  bins: { on: AggKey; ranges: AnalysisBin[] } | null
  region_level?: RegionLevel | null
}
export type RegionLevel = 'lower' | 'upper'
export type AnalysisRepeated = {
  keys: number
  rows: number
  same_value: number
  different_value: number
  samples: { period: string | null; target: string; rows: number[]; values: (number | string | null)[] }[]
}
export type AnalysisTarget = {
  index: number
  file_id: number
  file_name: string
  sheet_id: string | null
  sheet: string | null
  column_id: string | null
  column: string | null
  variant: string | null
  unit: string | null
  unit_source: string | null
  period: string | null
  period_source: string | null
  period_columns: string[]
  dims: string[]
  status: 'ok' | 'excluded' | 'needs_input'
  reasons: string[]
  candidates: { column_id: string; column: string; variant: string }[]
  data_rows: number
  rows_are_records: boolean
  repeated: AnalysisRepeated | null
}
export type AnalysisOverlap = {
  a: { file_id: number; file_name: string; sheet: string | null }
  b: { file_id: number; file_name: string; sheet: string | null }
  count: number
  same_value: number
  different_value: number
  samples: {
    period: string | null
    target: string
    a: { row: number; value: number | string | null }
    b: { row: number; value: number | string | null }
  }[]
}
export type AnalysisPreview = {
  field_key: string
  field_label: string
  stock: boolean
  variant: string | null
  variants: string[]
  unit: string | null
  units: string[]
  targets: AnalysisTarget[]
  problems: string[]
  warnings: string[]
  overlaps: AnalysisOverlap[]
  periods: string[]
  filter_values: Record<string, string[]>
  available_groups: string[]
  region_levels: {
    choice: RegionLevel | null
    mismatch: number
    conflicts: {
      period: string | null
      upper: { target: string; file_id: number; row: number; value: number | string | null; rows: number }
      lower_count: number
      lower_sum: number | null
      lower_missing: number
      difference: number | null // 상위 값 − 하위 합계 (확인할 수 없으면 null)
    }[]
  }
  base_dates: Record<string, string>
  rows: { candidate: number; excluded: Record<string, number> }
  reason_labels: Record<string, string>
  fingerprint: string
  limits: { targets: number; rows: number; groups: number; kept_rows: number }
}
export type AnalysisGroup = {
  keys: string[]
  count: number
  sum: number | null
  mean: number | null
  min: number | null
  max: number | null
  partial: boolean
  problem_rows: number
  files: number
  bin?: string
  bin_index?: number | null
}
export type AnalysisRunDetail = {
  id: number
  created_at: string
  created_by: string | null
  read_only: boolean
  conditions: {
    field_label: string
    unit: string | null
    variant: string | null
    periods: string[]
    stock: boolean
    group_by: string[]
    filters: AnalysisFilters
    aggregates: AggKey[]
    bins: { on: AggKey; on_label: string; ranges: (AnalysisBin & { label: string })[] } | null
    mean_denominator: string
    reason_labels: Record<string, string>
    warnings: string[]
    policy: string
    region_level?: RegionLevel | null
    level_conflicts?: AnalysisPreview['region_levels']['conflicts']
    base_dates?: Record<string, string>
    records?: string[]
  }
  inputs: (AnalysisTarget & { sha256: string })[]
  result: {
    group_fields: string[]
    group_labels: string[]
    period_split: boolean
    aggregates: AggKey[]
    bins: { index: number | null; label: string; groups: number }[] | null
    partial: boolean
    region_level?: RegionLevel | null
    level_mismatch?: number
    rows_included: number
    rows_excluded: number
    problem_rows: number
    reason_counts: Record<string, number>
    excluded_targets: AnalysisTarget[]
    detail_total: number
    excluded_total: number
    groups_total: number
  }
}
export type AnalysisRowRef = {
  file_id: number
  file_name: string
  sheet: string | null
  row: number
  period: string | null
}
export type AnalysisPage<T> = { total: number; offset: number; items: T[]; all?: number }

/** 409(실행 막힘)·422(조건 오류) 응답의 사유 목록을 함께 전한다 */
export class AnalysisProblemError extends Error {
  problems: string[]
  constructor(message: string, problems: string[]) {
    super(message)
    this.problems = problems
  }
}

async function analysisJson<T>(res: Response): Promise<T> {
  if (res.status === 409) {
    const detail = await res.json().then(
      (j: { detail?: { message?: string; problems?: string[] } | string }) => j.detail,
      () => undefined,
    )
    if (detail && typeof detail === 'object') {
      throw new AnalysisProblemError(detail.message ?? '실행할 수 없습니다.', detail.problems ?? [])
    }
    throw new AnalysisProblemError(typeof detail === 'string' ? detail : '실행할 수 없습니다.', [])
  }
  return json(res)
}

export async function fetchAnalysisFields(): Promise<AnalysisField[]> {
  return json(await apiFetch(`${API_BASE}/analysis/fields`))
}

export async function fetchAnalysisSources(fieldKey: string): Promise<AnalysisSource[]> {
  return json(await apiFetch(`${API_BASE}/analysis/sources?field_key=${encodeURIComponent(fieldKey)}`))
}

export async function previewAnalysis(body: AnalysisRequest): Promise<AnalysisPreview> {
  const res = await apiFetch(`${API_BASE}/analysis/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return analysisJson(res)
}

export async function runAnalysis(body: AnalysisRequest, fingerprint: string): Promise<{ id: number }> {
  const res = await apiFetch(`${API_BASE}/analysis/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, expected_fingerprint: fingerprint }),
  })
  return analysisJson(res)
}

export async function fetchAnalysisRun(id: number): Promise<AnalysisRunDetail> {
  return json(await apiFetch(`${API_BASE}/analysis/runs/${id}`))
}

export async function fetchAnalysisPage<T>(
  id: number,
  kind: 'groups' | 'excluded' | 'detail',
  offset: number,
  limit: number,
): Promise<AnalysisPage<T>> {
  return json(await apiFetch(`${API_BASE}/analysis/runs/${id}/${kind}?offset=${offset}&limit=${limit}`))
}

export function analysisDownloadUrl(id: number): string {
  return `${API_BASE}/analysis/runs/${id}/download`
}

export type AnalysisRunSummary = {
  id: number
  created_at: string
  field_label: string
  unit: string | null
  partial: boolean
  groups: number
  rows_included: number
  files: number
}

export async function fetchAnalysisRuns(): Promise<AnalysisRunSummary[]> {
  return json(await apiFetch(`${API_BASE}/analysis/runs`))
}

// ---------------------------------------------------------------- 파일별 시트로 묶기 (취합 방식 '파일별 시트')

export type BundleSheetRef = { file_id: number; sheet_id: string }

export type BundleLink = BundleSheetRef & {
  column_id?: string | null
  unit?: string | null
  period?: string | null
  rows_are_records?: boolean
  exclude?: boolean
  exclude_reason?: string | null
}

// variant·unit·region_level은 계산 엔진이 요구할 때만 보낸다 (variant ''는 '구분 없음')
export type BundleItem = {
  field_key: string
  aggregates: AggKey[]
  variant?: string | null
  unit?: string | null
  region_level?: 'lower' | 'upper' | null
  links?: BundleLink[]
}

// summary가 null이면 통계 요약 시트를 만들지 않는다 (서버도 분석 엔진을 부르지 않음)
export type BundleRequest = { file_ids: number[]; sheets: BundleSheetRef[]; summary: { items: BundleItem[] } | null }

export type BundlePlanFile = {
  file_id: number
  file_name: string
  sheets: { sheet_id: string; sheet: string; rows: number; columns: number }[]
  skipped_sheets: string[]
  pii: 'none' | 'acknowledged' | 'needs_ack'
  pii_summary: PiiSummary | null // 파일 목록과 같은 형식 (가린 값·위치)
  own: boolean
  problems: string[]
}

export type BundlePlanSheet = BundleSheetRef & {
  file_name: string
  sheet: string
  name: string // 결과 시트 이름
  renamed: string[] // 원래 이름에서 바꾼 이유
  rows: number
  kinds: { data: number; subtotal: number; note: number }
  empty_rows: number
  title_rows: number
  two_level_header: boolean
  columns: number
  formula_missing: number
  formula_cached: number
  formula_checked: boolean
  long_numbers: number
}

export type BundlePlanLink = BundleSheetRef & {
  file_name: string
  sheet: string
  output_sheet: string
  status: 'ok' | 'needs_input' | 'excluded' | 'user_excluded' | 'unchecked'
  reasons: string[]
  exclude_reason: string | null
  column_id?: string | null
  column?: string | null
  candidates?: { column_id: string; column: string; variant: string }[]
  field_label?: string
  unit?: string | null
  unit_source?: string | null
  period?: string | null
  period_source?: string | null
  period_columns?: string[]
  rows_are_records?: boolean
  // 같은 시트에서 같은 대상의 행이 반복된 정도 (있으면 '서로 다른 기록'인지 사용자가 확인해야 함)
  repeated?: { keys: number; rows: number; same_value: number; different_value: number } | null
}

export type BundleLevelConflict = {
  period: string | null
  upper: { target: string; file_id: number; row: number; value: number | string | null; rows: number }
  lower_count: number
  lower_sum: number | null
  lower_missing: number
  difference: number | null
}

export type BundlePlanItem = {
  field_key: string
  field_label: string
  aggregates: AggKey[]
  aggregate_labels: string[]
  choices: { variant: string | null; unit: string | null; region_level: 'lower' | 'upper' | null }
  variants: string[]
  units: string[]
  region_levels: { choice: 'lower' | 'upper' | null; mismatch: number; conflicts: BundleLevelConflict[] } | null
  unit: string | null
  periods: string[]
  links: BundlePlanLink[]
  coverage: { included: number; total: number; text: string }
  rows: { candidate: number; excluded: Record<string, number> } | null
  reason_labels: Record<string, string>
  warnings: string[]
  problems: string[]
  ready: boolean
}

export type BundlePlan = {
  mode: 'sheets'
  files: BundlePlanFile[]
  sheets: BundlePlanSheet[]
  summary: { sheet_name: string; items: BundlePlanItem[]; ready: boolean } | null
  fields: { key: string; label: string; sheets: number }[]
  problems: string[]
  warnings: string[]
  total_rows: number
  sheet_count: number
  limits: { files: number; sheets: number; rows: number; excel_rows: number; items: number; summary_sheets: number }
  basis: string
  ready: boolean
}

export async function planBundle(body: BundleRequest, signal?: AbortSignal): Promise<BundlePlan> {
  const res = await apiFetch(`${API_BASE}/bundle/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return json(res)
}

export async function runBundle(body: BundleRequest, basis: string): Promise<IntegrationRun> {
  const res = await apiFetch(`${API_BASE}/bundle/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, expected_basis: basis }),
  })
  return analysisJson(res) // 409는 사유 목록과 함께 (AnalysisProblemError)
}
