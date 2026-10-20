import { useState } from 'react'
import { mapFile, type FileMapping, type MappingStatus } from './api'
import { STATUS_TEXT } from './mappingText'
import { ProgressPanel } from './Progress'
import { MAPPING_STAGES } from './progressStages'
import { navigate } from './router'

/** 업로드한 파일의 열 매핑: 분석 실행 + 시트별 현황 요약. 확인·확정은 열 매핑 작업 화면(/mapping)에서 한다. */
function Mapping({ fileId }: { fileId: number }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mapping, setMapping] = useState<FileMapping | null>(null)
  const href = `/mapping?file=${fileId}`

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

  const count = (columns: { status: MappingStatus }[], s: MappingStatus) => columns.filter((c) => c.status === s).length

  return (
    <div className="mapping">
      <div className="mapping-actions">
        <button type="button" className="mapping-run" onClick={() => void run()} disabled={busy}>
          {busy ? '열 매핑 분석 중… (LLM 사용 시 수십 초)' : mapping ? '열 매핑 다시 분석' : '열 매핑 분석'}
        </button>
        <a
          className="mapping-confirm mw-link"
          href={href}
          onClick={(e) => {
            e.preventDefault()
            navigate(href, { focus: { to: 'page' } })
          }}
        >
          열 매핑 화면에서 확인·확정
        </a>
      </div>
      {busy && <ProgressPanel stages={MAPPING_STAGES} />}
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
              {mapping.llm === 'unavailable' &&
                'LLM을 사용할 수 없어 일부 열이 미해결로 남았습니다 (설정의 LLM 제공자·연결 확인).'}
            </p>
          )}
          {mapping.sheets.map((sheet) => (
            <p key={sheet.name} className="mapping-sheet mapping-summary">
              <strong>{sheet.name}</strong>{' '}
              {(['confirmed', 'auto', 'review', 'unresolved'] as MappingStatus[]).map((s) => (
                <span key={s} className={`upload-tag mapping-${s}`}>
                  {STATUS_TEXT[s]} {count(sheet.columns, s)}
                </span>
              ))}
            </p>
          ))}
          <p className="mw-muted">
            자동 제안은 아직 확정 전입니다. '열 매핑 화면에서 확인·확정'에서 실제 데이터를 보며 확정하세요.
          </p>
        </>
      )}
    </div>
  )
}

export default Mapping
