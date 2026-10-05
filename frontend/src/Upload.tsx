import { useRef, useState, type DragEvent } from 'react'
import { ACCEPTED_EXTENSIONS, uploadFiles, type Cell, type RowKind, type SheetSummary, type UploadResult } from './api'

const KIND_LABEL: Record<RowKind, string> = { data: '데이터', empty: '빈 행', subtotal: '소계/합계', note: '주석' }

function isAccepted(file: File) {
  const name = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))
}

function formatCell(value: Cell) {
  if (value === null) return ''
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

function Upload() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<UploadResult[]>([])

  async function handleFiles(list: FileList | null) {
    if (!list || list.length === 0 || busy) return
    const files = Array.from(list)
    const rejected = files.filter((f) => !isAccepted(f)).map((f) => f.name)
    const accepted = files.filter(isAccepted)
    setError(rejected.length ? `지원하지 않는 형식이라 제외했습니다: ${rejected.join(', ')}` : null)
    if (accepted.length === 0) return

    setBusy(true)
    try {
      const uploaded = await uploadFiles(accepted)
      setResults((prev) => [...uploaded, ...prev])
    } catch (err) {
      setError(`업로드 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(false)
    void handleFiles(e.dataTransfer.files)
  }

  return (
    <section className="upload">
      <h2>파일 업로드</h2>
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
        <p className="upload-drop-title">{busy ? '업로드·분석 중…' : '파일을 여기에 끌어다 놓으세요'}</p>
        <p className="upload-drop-hint">또는 눌러서 선택 · xlsx, xls, csv · 여러 개 가능 · 파일당 최대 50MB</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(',')}
          hidden
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </div>

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {results.length > 0 && (
        <ul className="upload-results">
          {results.map((r, i) => (
            <li key={`${r.id ?? 'x'}-${i}`} className="upload-file">
              <FileSummary result={r} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function FileSummary({ result }: { result: UploadResult }) {
  return (
    <>
      <div className="upload-file-head">
        <strong className="upload-file-name">{result.original_name}</strong>
        {result.error ? (
          <span className="upload-error">{result.error}</span>
        ) : (
          <span className="upload-file-meta">
            시트 {result.sheet_count}개 · 데이터 {result.data_rows.toLocaleString('ko-KR')}행
            {result.encoding && ` · ${result.encoding.toUpperCase()}`}
          </span>
        )}
      </div>
      {result.sheets.map((sheet) => (
        <SheetPreview key={sheet.name} sheet={sheet} />
      ))}
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
