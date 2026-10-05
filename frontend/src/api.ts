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
