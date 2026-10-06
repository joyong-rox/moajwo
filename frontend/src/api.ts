// 백엔드 API는 항상 /api 경로로 호출한다.
// 개발: Vite proxy, 운영: nginx proxy가 /api 접두어를 떼고 백엔드로 전달한다.
const API_BASE = '/api'

export type Health = {
  status: string
  database: string
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${API_BASE}/health`, { signal })
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
export type MappingMethod = 'exact' | 'learned' | 'exact_part' | 'similarity' | 'pattern' | 'llm' | 'user' | 'none'
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
  const res = await fetch(`${API_BASE}/files/${fileId}/mapping`, { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => null)
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return (await res.json()) as FileMapping
}

export type StandardFieldInfo = { key: string; label: string; description: string }

export async function fetchFields(): Promise<StandardFieldInfo[]> {
  const res = await fetch(`${API_BASE}/mapping/fields`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as StandardFieldInfo[]
}

export type DecisionInput = { sheet: string; column: string; field_key: string | null }
export type DecisionsResult = { saved: number; learned: string[]; not_learned: { column: string; reason: string }[] }

export async function saveDecisions(fileId: number, decisions: DecisionInput[]): Promise<DecisionsResult> {
  const res = await fetch(`${API_BASE}/files/${fileId}/mapping/decisions`, {
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

export async function uploadFiles(files: File[]): Promise<UploadResult[]> {
  const body = new FormData()
  for (const file of files) body.append('files', file, file.name)
  const res = await fetch(`${API_BASE}/files`, { method: 'POST', body })
  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => null)
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return (await res.json()) as UploadResult[]
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
  return json(await fetch(`${API_BASE}/files`))
}

export async function acknowledgePii(fileId: number): Promise<PiiSummary> {
  return json(await fetch(`${API_BASE}/files/${fileId}/pii/acknowledge`, { method: 'POST' }))
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
    await fetch(`${API_BASE}/validation`, {
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
  return json(await fetch(`${API_BASE}/glossary/info`))
}

export async function listEntries(q: string, scope: 'all' | 'org' | 'region' = 'all'): Promise<GlossaryEntry[]> {
  const params = new URLSearchParams({ scope, limit: '100' })
  if (q.trim()) params.set('q', q.trim())
  return json(await fetch(`${API_BASE}/glossary/entries?${params}`))
}

export async function deleteEntry(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/glossary/entries/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export async function importGlossary(file: File): Promise<ImportReport> {
  const body = new FormData()
  body.append('file', file, file.name)
  return json(await fetch(`${API_BASE}/glossary/import`, { method: 'POST', body }))
}

export async function listAliases(): Promise<GlossaryAlias[]> {
  return json(await fetch(`${API_BASE}/glossary/aliases`))
}

export async function createAlias(body: AliasInput): Promise<GlossaryAlias> {
  return json(
    await fetch(`${API_BASE}/glossary/aliases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteAlias(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/glossary/aliases/${id}`, { method: 'DELETE' })
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
  learned: { columns: number; names: number } | null
  parent_id: number | null
  child_id: number | null
  history: { id: number; status: string; score: number; blocks: number; pending_open: number }[]
  changes: ReviewChanges | null
  automation_id: number | null
  automation_name: string | null
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
    await fetch(`${API_BASE}/reviews`, {
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
    await fetch(`${API_BASE}/reviews/${reviewId}/items/${itemId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, choice }),
    }),
  )
}

export async function setRememberFormat(reviewId: number, remember: boolean): Promise<Review> {
  return json(
    await fetch(`${API_BASE}/reviews/${reviewId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remember_format: remember }),
    }),
  )
}

export async function completeReview(reviewId: number): Promise<Review> {
  return json(await fetch(`${API_BASE}/reviews/${reviewId}/complete`, { method: 'POST' }))
}

export async function recheckReview(reviewId: number, replace: Record<number, number>): Promise<Review> {
  return json(
    await fetch(`${API_BASE}/reviews/${reviewId}/recheck`, {
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
  return json(await fetch(`${API_BASE}/reviews/${reviewId}/integrate`, { method: 'POST' }))
}

export async function listRuns(reviewId: number): Promise<IntegrationRun[]> {
  return json(await fetch(`${API_BASE}/runs?review_id=${reviewId}`))
}

export async function getReview(reviewId: number): Promise<Review> {
  return json(await fetch(`${API_BASE}/reviews/${reviewId}`))
}

export async function listAllRuns(limit = 100): Promise<IntegrationRun[]> {
  return json(await fetch(`${API_BASE}/runs?limit=${limit}`))
}

export type ColumnRule = { key: string; column: string; field_key: string | null; field_label: string | null }
export type Automation = {
  id: number
  name: string
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
    await fetch(`${API_BASE}/runs/${runId}/automation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  )
}

export async function listAutomations(): Promise<Automation[]> {
  return json(await fetch(`${API_BASE}/automations`))
}

export async function deleteAutomation(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/automations/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export async function runAutomation(
  id: number,
  body: { file_ids: number[]; baseline_file_ids: number[]; expected_period: string | null },
): Promise<AutomationRunResult> {
  return json(
    await fetch(`${API_BASE}/automations/${id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}
