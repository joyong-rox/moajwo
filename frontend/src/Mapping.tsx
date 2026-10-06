import { useState } from 'react'
import {
  fetchFields,
  mapFile,
  saveDecisions,
  type Cell,
  type ColumnMapping,
  type DecisionInput,
  type FileMapping,
  type MappingMethod,
  type MappingStatus,
  type StandardFieldInfo,
} from './api'

const STATUS_LABEL: Record<MappingStatus, string> = {
  auto: '자동 적용',
  review: '확인 필요',
  unresolved: '미해결',
  confirmed: '확정',
}
const METHOD_LABEL: Record<MappingMethod, string> = {
  exact: '사전 일치',
  standard: '전사 표준 사전',
  learned: '학습된 이름',
  exact_part: '사전 부분 일치',
  similarity: '유사도',
  pattern: '값 형식 규칙',
  llm: 'LLM',
  user: '사용자 확정',
  none: '-',
}
// select 값: '' = 아직 고르지 않음, NONE = '해당 없음'으로 확정
const NONE = '__none__'

let fieldsPromise: Promise<StandardFieldInfo[]> | null = null
function loadFields() {
  fieldsPromise ??= fetchFields().catch((err: unknown) => {
    fieldsPromise = null
    throw err
  })
  return fieldsPromise
}

function formatSample(value: Cell) {
  if (value === null) return ''
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

function score(value: number | null) {
  return value === null ? '-' : value.toFixed(2)
}

function Mapping({ fileId }: { fileId: number }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mapping, setMapping] = useState<FileMapping | null>(null)
  const [fields, setFields] = useState<StandardFieldInfo[]>([])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const [result, list] = await Promise.all([mapFile(fileId), loadFields()])
      setMapping(result)
      setFields(list)
    } catch (err) {
      setError(`매핑 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
  }

  function markConfirmed(sheetName: string, decisions: DecisionInput[]) {
    const labels = new Map(fields.map((f) => [f.key, f.label]))
    setMapping((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        sheets: prev.sheets.map((sheet) => {
          if (sheet.name !== sheetName) return sheet
          return {
            ...sheet,
            columns: sheet.columns.map((c) => {
              const d = decisions.find((x) => x.column === c.column)
              if (!d) return c
              return {
                ...c,
                status: 'confirmed' as const,
                method: 'user' as const,
                field_key: d.field_key,
                field_label: d.field_key ? (labels.get(d.field_key) ?? d.field_key) : null,
                score: d.field_key ? 1 : null,
                reason: d.field_key ? '사용자가 확정' : "사용자가 '해당 없음'으로 확정",
              }
            }),
          }
        }),
      }
    })
  }

  return (
    <div className="mapping">
      <button type="button" className="mapping-run" onClick={() => void run()} disabled={busy}>
        {busy ? '열 매핑 분석 중… (LLM 사용 시 수십 초)' : mapping ? '열 매핑 다시 분석' : '열 매핑 분석'}
      </button>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {mapping && (
        <>
          {(mapping.embedding === 'unavailable' || mapping.llm === 'unavailable') && (
            <p className="mapping-warn" role="status">
              {mapping.embedding === 'unavailable' && '임베딩(bge-m3)을 사용할 수 없어 문자열 유사도만 사용했습니다. '}
              {mapping.llm === 'unavailable' && 'LLM(Ollama)을 사용할 수 없어 일부 열이 미해결로 남았습니다.'}
            </p>
          )}
          {mapping.sheets.map((sheet) => (
            <SheetMapping
              key={sheet.name}
              fileId={fileId}
              name={sheet.name}
              columns={sheet.columns}
              fields={fields}
              onConfirmed={markConfirmed}
            />
          ))}
        </>
      )}
    </div>
  )
}

type SheetProps = {
  fileId: number
  name: string
  columns: ColumnMapping[]
  fields: StandardFieldInfo[]
  onConfirmed: (sheet: string, decisions: DecisionInput[]) => void
}

function initialChoice(c: ColumnMapping) {
  if (c.status === 'confirmed' && c.field_key === null) return NONE
  return c.field_key ?? ''
}

function SheetMapping({ fileId, name, columns, fields, onConfirmed }: SheetProps) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const count = (s: MappingStatus) => columns.filter((c) => c.status === s).length
  const choice = (c: ColumnMapping) => draft[c.column] ?? initialChoice(c)
  const pendingSuggestions = columns.filter((c) => c.status !== 'confirmed' && c.field_key !== null)

  async function confirm(items: { column: string; value: string }[]) {
    const decisions: DecisionInput[] = items.map(({ column, value }) => ({
      sheet: name,
      column,
      field_key: value === NONE ? null : value,
    }))
    setSaving(true)
    setError(null)
    try {
      const res = await saveDecisions(fileId, decisions)
      onConfirmed(name, decisions)
      setDraft((prev) => {
        const next = { ...prev }
        for (const d of decisions) delete next[d.column]
        return next
      })
      const learned = res.learned.length ? ` · 새로 학습한 열 이름 ${new Set(res.learned).size}개` : ''
      setMessage(`${res.saved}개 열 확정 저장${learned}`)
    } catch (err) {
      setError(`저장 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="mapping-sheet">
      <h3>
        <span>{name}</span>
        <span className="upload-tag mapping-confirmed">확정 {count('confirmed')}</span>
        <span className="upload-tag mapping-auto">자동 {count('auto')}</span>
        <span className="upload-tag mapping-review">확인 {count('review')}</span>
        <span className="upload-tag mapping-unresolved">미해결 {count('unresolved')}</span>
      </h3>
      <div className="mapping-actions">
        <button
          type="button"
          className="mapping-confirm"
          disabled={saving || pendingSuggestions.length === 0}
          onClick={() => void confirm(pendingSuggestions.map((c) => ({ column: c.column, value: choice(c) })))}
        >
          제안대로 모두 확정 ({pendingSuggestions.length})
        </button>
        {message && (
          <span className="mapping-message" role="status">
            {message}
          </span>
        )}
        {error && (
          <span className="upload-error" role="alert">
            {error}
          </span>
        )}
      </div>
      <div className="upload-table-wrap">
        <table className="upload-table mapping-table">
          <thead>
            <tr>
              <th scope="col">원본 열</th>
              <th scope="col">매핑 (확정)</th>
              <th scope="col">상태</th>
              <th scope="col">방법 · 점수</th>
              <th scope="col">후보 (유사도)</th>
              <th scope="col">샘플</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((c, i) => {
              const value = choice(c)
              const changed = value !== initialChoice(c)
              return (
                <tr key={`${c.column}-${i}`} className={`mapping-row-${c.status}`}>
                  <th scope="row">{c.column}</th>
                  <td>
                    <div className="mapping-pick">
                      <select
                        aria-label={`${c.column} 표준 항목`}
                        value={value}
                        disabled={saving}
                        onChange={(e) => setDraft((prev) => ({ ...prev, [c.column]: e.target.value }))}
                      >
                        <option value="">— 선택 —</option>
                        {fields.map((f) => (
                          <option key={f.key} value={f.key}>
                            {f.label}
                          </option>
                        ))}
                        <option value={NONE}>해당 없음</option>
                      </select>
                      <button
                        type="button"
                        className="mapping-confirm"
                        disabled={saving || value === '' || (c.status === 'confirmed' && !changed)}
                        onClick={() => void confirm([{ column: c.column, value }])}
                      >
                        확정
                      </button>
                    </div>
                    {c.qualifier && <span className="upload-tag">{c.qualifier}</span>}
                  </td>
                  <td>
                    <span className={`upload-tag mapping-${c.status}`} title={c.reason ?? undefined}>
                      {STATUS_LABEL[c.status]}
                    </span>
                  </td>
                  <td title={c.reason ?? undefined}>
                    {METHOD_LABEL[c.method]} · {score(c.score)}
                    {c.reason && <div className="mapping-reason">{c.reason}</div>}
                  </td>
                  <td>
                    {c.candidates.map((cand) => (
                      <span
                        key={cand.key}
                        className="mapping-candidate"
                        title={`문자열 ${cand.fuzzy.toFixed(2)} / 임베딩 ${cand.embedding === null ? '-' : cand.embedding.toFixed(2)}`}
                      >
                        {cand.label} {cand.score.toFixed(2)}
                      </span>
                    ))}
                  </td>
                  <td className="mapping-samples">{c.samples.map(formatSample).join(', ')}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default Mapping
