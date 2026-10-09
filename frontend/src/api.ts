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
  error: string | null
}

export type PiiType = 'rrn' | 'mobile' | 'email'
export const PII_LABEL: Record<PiiType, string> = { rrn: '주민등록번호', mobile: '휴대전화번호', email: '이메일' }
export type PiiItem = { type: PiiType; sheet: string; row: number; column: string; masked: string }
export type PiiSummary = { counts: Record<PiiType, number>; items: PiiItem[]; acknowledged: boolean }

export type MappingStatus = 'auto' | 'review' | 'unresolved' | 'confirmed'
export type MappingMethod = 'exact' | 'standard' | 'learned' | 'exact_part' | 'similarity' | 'pattern' | 'llm' | 'user' | 'none'
export type EngineStatus = 'ok' | 'unavailable' | 'skipped'

export type MappingCandidate = {
  key: string
  label: string
  score: number
  fuzzy: number
  embedding: number | null
}

export type ColumnMapping = {
  column: string
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
  sheets: { name: string; columns: ColumnMapping[] }[]
}

export async function mapFile(fileId: number): Promise<FileMapping> {
  const res = await apiFetch(`${API_BASE}/files/${fileId}/mapping`, { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => null)
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return (await res.json()) as FileMapping
}

export type StandardFieldInfo = { key: string; label: string; description: string }

export async function fetchFields(): Promise<StandardFieldInfo[]> {
  const res = await apiFetch(`${API_BASE}/mapping/fields`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as StandardFieldInfo[]
}

export type DecisionInput = { sheet: string; column: string; field_key: string | null }
export type DecisionsResult = { saved: number; learned: string[]; not_learned: { column: string; reason: string }[] }

export async function saveDecisions(fileId: number, decisions: DecisionInput[]): Promise<DecisionsResult> {
  const res = await apiFetch(`${API_BASE}/files/${fileId}/mapping/decisions`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => null)
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
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => null)
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

export type StoredFile = { id: number; original_name: string; uploaded_at: string; pii: PiiSummary | null }

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
  pii: { file_id: number; file_name: string; counts: Record<PiiType, number>; items: PiiItem[]; acknowledged: boolean }[]
  notes: string[]
  entities: EntityNote[]
  created_at: string
}

export type EntityCandidate = { code: string; name: string; type: string }
export type EntityNote = {
  value: string
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
  source: 'example' | 'upload' | 'user'
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
  history: { id: number; status: string; score: number; blocks: number; pending_open: number }[]
  changes: ReviewChanges | null
  automation_id: number | null
  automation_name: string | null
  created_by: number | null
  created_by_name: string | null
  can_edit: boolean
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
  }
  error: string | null
  created_at: string
}

export const runDownloadUrl = (runId: number) => `${API_BASE}/runs/${runId}/download`

export async function integrateReview(reviewId: number): Promise<IntegrationRun> {
  return json(await apiFetch(`${API_BASE}/reviews/${reviewId}/integrate`, { method: 'POST' }))
}

export async function listRuns(reviewId: number): Promise<IntegrationRun[]> {
  return json(await apiFetch(`${API_BASE}/runs?review_id=${reviewId}`))
}

export async function getReview(reviewId: number): Promise<Review> {
  return json(await apiFetch(`${API_BASE}/reviews/${reviewId}`))
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

export type Role = 'user' | 'admin' | 'owner'
export const ROLE_LABEL: Record<Role, string> = { user: '일반 사용자', admin: '관리자', owner: '소유자' }
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

export async function login(username: string, password: string): Promise<Me> {
  return json(await apiFetch(`${API_BASE}/auth/login`, { method: 'POST', ...jsonBody({ username, password }) }))
}

export async function setupOwner(body: { username: string; display_name: string; password: string }): Promise<Me> {
  return json(await apiFetch(`${API_BASE}/auth/setup`, { method: 'POST', ...jsonBody(body) }))
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

export type UserAccount = {
  id: number
  username: string
  display_name: string
  role: Role
  role_label: string
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export async function listUsers(): Promise<UserAccount[]> {
  return json(await apiFetch(`${API_BASE}/users`))
}

export async function createUser(body: {
  username: string
  display_name: string
  role: Role
  password: string
}): Promise<UserAccount> {
  return json(await apiFetch(`${API_BASE}/users`, { method: 'POST', ...jsonBody(body) }))
}

export async function updateUser(
  id: number,
  body: { display_name?: string; role?: Role; is_active?: boolean },
): Promise<UserAccount> {
  return json(await apiFetch(`${API_BASE}/users/${id}`, { method: 'PATCH', ...jsonBody(body) }))
}

export async function resetPassword(id: number, password: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/users/${id}/password`, { method: 'POST', ...jsonBody({ password }) })
  if (!res.ok) await json(res)
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
  return json(await apiFetch(`${API_BASE}/admin/promotions/${id}/${decision}`, { method: 'POST', ...jsonBody({ note }) }))
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
  return json(await apiFetch(`${API_BASE}/settings`, { method: 'PUT', ...jsonBody({ manual_baseline_minutes: minutes }) }))
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
