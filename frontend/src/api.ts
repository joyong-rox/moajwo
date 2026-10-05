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
  error: string | null
}

export type MappingStatus = 'auto' | 'review' | 'unresolved'
export type MappingMethod = 'exact' | 'exact_part' | 'similarity' | 'llm' | 'none'
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
