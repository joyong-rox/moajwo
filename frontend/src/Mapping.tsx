import { useState } from 'react'
import { mapFile, type Cell, type ColumnMapping, type FileMapping, type MappingMethod, type MappingStatus } from './api'

const STATUS_LABEL: Record<MappingStatus, string> = { auto: '자동 적용', review: '확인 필요', unresolved: '미해결' }
const METHOD_LABEL: Record<MappingMethod, string> = {
  exact: '사전 일치',
  exact_part: '사전 부분 일치',
  similarity: '유사도',
  llm: 'LLM',
  none: '-',
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

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setMapping(await mapFile(fileId))
    } catch (err) {
      setError(`매핑 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
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
      {mapping && <MappingResult mapping={mapping} />}
    </div>
  )
}

function MappingResult({ mapping }: { mapping: FileMapping }) {
  return (
    <>
      {(mapping.embedding === 'unavailable' || mapping.llm === 'unavailable') && (
        <p className="mapping-warn" role="status">
          {mapping.embedding === 'unavailable' && '임베딩(bge-m3)을 사용할 수 없어 문자열 유사도만 사용했습니다. '}
          {mapping.llm === 'unavailable' && 'LLM(Ollama)을 사용할 수 없어 일부 열이 미해결로 남았습니다.'}
        </p>
      )}
      {mapping.sheets.map((sheet) => (
        <SheetMapping key={sheet.name} name={sheet.name} columns={sheet.columns} />
      ))}
    </>
  )
}

function SheetMapping({ name, columns }: { name: string; columns: ColumnMapping[] }) {
  const count = (s: MappingStatus) => columns.filter((c) => c.status === s).length
  return (
    <section className="mapping-sheet">
      <h3>
        <span>{name}</span>
        <span className="upload-tag mapping-auto">자동 {count('auto')}</span>
        <span className="upload-tag mapping-review">확인 {count('review')}</span>
        <span className="upload-tag mapping-unresolved">미해결 {count('unresolved')}</span>
      </h3>
      <div className="upload-table-wrap">
        <table className="upload-table mapping-table">
          <thead>
            <tr>
              <th scope="col">원본 열</th>
              <th scope="col">매핑 결과</th>
              <th scope="col">상태</th>
              <th scope="col">방법 · 점수</th>
              <th scope="col">후보 (유사도)</th>
              <th scope="col">샘플</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((c, i) => (
              <tr key={`${c.column}-${i}`} className={`mapping-row-${c.status}`}>
                <th scope="row">{c.column}</th>
                <td>
                  {c.field_label ?? '-'}
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
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default Mapping
