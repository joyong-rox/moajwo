import { useRef, useState, type DragEvent } from 'react'
import Mapping from './Mapping'
import PiiNotice from './PiiNotice'
import { ProgressPanel, SkeletonRows } from './Progress'
import { UPLOAD_STAGES } from './progressStages'
import { ACCEPTED_EXTENSIONS, uploadFiles, type Cell, type RowKind, type SheetSummary, type UploadResult } from './api'
import { navigate } from './router'
import { Banner, Icon, NavLink } from './Workflow'

const KIND_LABEL: Record<RowKind, string> = { data: '데이터', empty: '빈 행', subtotal: '소계/합계', note: '주석' }
const MAX_TARGETS = 50 // 홈에서 한 번에 검사할 수 있는 파일 수 (Validation과 같음)

function isAccepted(file: File) {
  const name = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))
}

function formatCell(value: Cell) {
  if (value === null) return ''
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

function Upload({ onUploaded, onPiiChanged }: { onUploaded?: () => void; onPiiChanged?: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<UploadResult[]>([])
  // 처리 중인 파일 이름과 실제 업로드 진행률 (0~1). 브라우저가 진행률을 알려주지 않으면 null → 시간 기준 단계 표시
  const [pending, setPending] = useState<string[]>([])
  const [sent, setSent] = useState<number | null>(null)

  async function handleFiles(list: FileList | null) {
    if (!list || list.length === 0 || busy) return
    const files = Array.from(list)
    const rejected = files.filter((f) => !isAccepted(f)).map((f) => f.name)
    const accepted = files.filter(isAccepted)
    setError(rejected.length ? `지원하지 않는 형식이라 제외했습니다: ${rejected.join(', ')}` : null)
    if (accepted.length === 0) return

    setBusy(true)
    setPending(accepted.map((f) => f.name))
    setSent(null)
    try {
      const uploaded = await uploadFiles(accepted, setSent)
      setResults((prev) => [...uploaded, ...prev])
      onUploaded?.()
    } catch (err) {
      setError(`업로드 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
      setPending([])
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(false)
    void handleFiles(e.dataTransfer.files)
  }

  const okIds = results.filter((r) => r.id !== null && !r.error).map((r) => r.id as number)
  const failed = results.filter((r) => r.error).length

  return (
    <section className="upload wk-upload">
      <h2>파일 업로드</h2>
      <p className="wk-card-desc">올린 원본은 바꾸지 않고 읽기만 합니다. 통합 결과는 언제나 새 파일로 만듭니다.</p>
      <div
        className={`upload-drop${dragging ? ' is-dragging' : ''}${busy ? ' is-busy' : ''}`}
        role="button"
        tabIndex={0}
        aria-label="xlsx, xls, csv 파일을 끌어다 놓거나 눌러서 선택"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            inputRef.current?.click()
          }
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span className="wk-drop-icon" aria-hidden="true">
          <Icon name="upload" />
        </span>
        <div className="wk-drop-text">
          <p className="upload-drop-title">{busy ? '업로드·분석 중…' : '파일을 여기에 끌어다 놓으세요'}</p>
          <p className="upload-drop-hint">또는 눌러서 선택 · xlsx, xls, csv · 여러 개 가능 · 파일당 최대 50MB</p>
        </div>
        {!busy && (
          <span className="wk-drop-button" aria-hidden="true">
            파일 선택
          </span>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(',')}
          hidden
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </div>

      {busy && (
        <>
          <ProgressPanel
            stages={UPLOAD_STAGES}
            uploadFraction={sent ?? undefined}
            title={`${pending.length}개 파일 처리 중 — 끝나면 행 수·헤더·매핑 분석이 아래에 표시됩니다`}
          />
          <SkeletonRows names={pending} />
        </>
      )}

      {error && (
        <Banner tone="block" icon="block" title="올리지 못한 파일이 있습니다" live="alert">
          <p>{error}</p>
        </Banner>
      )}

      {results.length > 0 && (
        <>
          <h3 className="wk-subhead">
            이번에 올린 파일 {results.length}개{failed > 0 && <span className="wk-head-sub"> · 실패 {failed}개</span>}
          </h3>
          <ul className="upload-results">
            {results.map((r, i) => (
              <li key={`${r.id ?? 'x'}-${i}`} className={`upload-file wk-filecard${r.error ? ' is-failed' : ''}`}>
                <FileSummary result={r} onPiiChanged={onPiiChanged} />
              </li>
            ))}
          </ul>
          {okIds.length > 0 && (
            <div className="wk-actionbar" role="region" aria-label="다음 단계">
              <span className="wk-actionbar-item">
                <strong>다음</strong> 열 매핑을 확인한 뒤 검사·검토합니다. 자동 제안은 아직 확정 전입니다.
              </span>
              <NavLink className="mapping-confirm" href={`/mapping?file=${okIds[0]}`}>
                열 매핑 열기
              </NavLink>
              {/* 한도를 넘으면 앞 50개만 고르지 않는다 — 목록에서 직접 고르도록 안내 */}
              <button
                type="button"
                className="mapping-run wk-next"
                disabled={okIds.length > MAX_TARGETS}
                onClick={() => navigate(`/?files=${okIds.join(',')}`)}
              >
                올린 파일로 검사 대상 고르기 ({okIds.length})
              </button>
              {okIds.length > MAX_TARGETS && (
                <span className="wk-actionbar-item mw-muted">
                  한 번에 {MAX_TARGETS}개까지 검사할 수 있어 바로 고르지 않습니다 — 아래 검사 목록에서 직접 고르세요.
                </span>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}

function FileSummary({ result, onPiiChanged }: { result: UploadResult; onPiiChanged?: () => void }) {
  return (
    <>
      <div className="upload-file-head">
        <Icon name="file" />
        <strong className="upload-file-name">
          {result.id !== null && `#${result.id} `}
          {result.original_name}
        </strong>
        <span className={`wk-badge ${result.error ? 'tone-block' : 'tone-ok'}`}>
          <Icon name={result.error ? 'block' : 'check'} /> {result.error ? '실패' : '올림 완료'}
        </span>
        {result.error ? (
          <span className="upload-error">{result.error}</span>
        ) : (
          <span className="upload-file-meta">
            시트 {result.sheet_count}개 · 데이터 {result.data_rows.toLocaleString('ko-KR')}행
            {result.encoding && ` · ${result.encoding.toUpperCase()}`}
            {result.skipped_sheets && result.skipped_sheets.length > 0 &&
              ` · 설명 시트 건너뜀: ${result.skipped_sheets.join(', ')}`}
          </span>
        )}
      </div>
      {result.id !== null && result.pii && (
        <PiiNotice fileId={result.id} pii={result.pii} onAcknowledged={onPiiChanged} />
      )}
      {result.sheets.map((sheet) => (
        <SheetPreview key={sheet.name} sheet={sheet} />
      ))}
      {result.id !== null && !result.error && <Mapping fileId={result.id} />}
    </>
  )
}

function headerLabel(sheet: SheetSummary) {
  if (!sheet.header_row) return '헤더 없음'
  if (sheet.header_row_end && sheet.header_row_end !== sheet.header_row) {
    return `헤더 ${sheet.header_row}~${sheet.header_row_end}행`
  }
  return `헤더 ${sheet.header_row}행`
}

function SheetPreview({ sheet }: { sheet: SheetSummary }) {
  const shown = sheet.data_rows + sheet.empty_rows + sheet.subtotal_rows + sheet.note_rows
  return (
    <details className="upload-sheet">
      <summary>
        <span className="upload-sheet-name">{sheet.name}</span>
        <span>{headerLabel(sheet)}</span>
        <span>데이터 {sheet.data_rows.toLocaleString('ko-KR')}행</span>
        {sheet.subtotal_rows > 0 && <span className="upload-tag kind-subtotal">소계/합계 {sheet.subtotal_rows}</span>}
        {sheet.empty_rows > 0 && <span className="upload-tag kind-empty">빈 행 {sheet.empty_rows}</span>}
        {sheet.note_rows > 0 && <span className="upload-tag kind-note">주석 {sheet.note_rows}</span>}
        {sheet.missing_cells > 0 && <span className="upload-tag kind-missing">결측 표기 {sheet.missing_cells}칸</span>}
      </summary>
      <div className="upload-table-wrap">
        <table className="upload-table">
          <thead>
            <tr>
              <th scope="col">행</th>
              {sheet.columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.preview.map((row) => (
              <tr key={row.row} className={`kind-${row.kind}`}>
                <th scope="row" title={KIND_LABEL[row.kind]}>
                  {row.row}
                  {row.kind !== 'data' && <span className="upload-tag">{KIND_LABEL[row.kind]}</span>}
                </th>
                {row.kind === 'note' ? (
                  // 가로 병합된 주석은 ffill로 모든 칸에 반복되므로 한 번만 표시한다.
                  <td colSpan={row.cells.length}>
                    {formatCell(row.cells.find((c) => c !== null) ?? null)}
                  </td>
                ) : (
                  row.cells.map((cell, j) => {
                    const missing = row.missing.includes(j)
                    const cls = missing ? 'num missing' : typeof cell === 'number' ? 'num' : undefined
                    return (
                      <td key={j} className={cls} title={missing ? '결측/비공개 표기 (원본 그대로)' : undefined}>
                        {formatCell(cell)}
                      </td>
                    )
                  })
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown > sheet.preview.length && (
        <p className="upload-drop-hint">처음 {sheet.preview.length}행만 미리보기로 표시합니다.</p>
      )}
    </details>
  )
}

export default Upload
