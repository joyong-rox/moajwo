import { useEffect, useId, useRef, useState, type DragEvent } from 'react'
import Mapping from './Mapping'
import PiiNotice from './PiiNotice'
import { ProgressPanel, SkeletonRows } from './Progress'
import { UPLOAD_STAGES } from './progressStages'
import {
  ACCEPTED_EXTENSIONS,
  fetchFileLimits,
  uploadFiles,
  type Cell,
  type FileLimits,
  type RowKind,
  type SheetSummary,
  type UploadResult,
} from './api'
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
  // 화면 읽기 도구에 전할 업로드 상태 (이 요청의 시작·결과만, 건수만 — 파일 이름은 넣지 않음).
  // 상태 영역은 처음부터 화면에 두고 내용만 바꾼다. 시작 → 결과는 글자가 달라 바로 바꾸고,
  // 직전과 같은 글자일 때만 비웠다가 다시 채워 같은 문구도 새로 전달되게 한다 (다시 그리기만으로는 바뀌지 않음).
  const [announcement, setAnnouncement] = useState('')
  const shown = useRef('')
  const announceTimer = useRef<number | undefined>(undefined)
  const titleId = useId()
  const hintId = useId()
  const busyId = useId()
  // 서버의 읽기 한도 (환경마다 다름). 못 불러오면 크기 안내만 빼고 보인다 — 실제 거절은 서버가 사유와 함께 알린다
  const [limits, setLimits] = useState<FileLimits | null>(null)

  useEffect(() => () => window.clearTimeout(announceTimer.current), [])
  useEffect(() => {
    fetchFileLimits()
      .then(setLimits)
      .catch(() => undefined)
  }, [])

  function announce(message: string) {
    window.clearTimeout(announceTimer.current)
    const set = (text: string) => {
      shown.current = text
      setAnnouncement(text)
    }
    if (message !== '' && message === shown.current) {
      set('')
      announceTimer.current = window.setTimeout(() => set(message), 100)
    } else {
      set(message)
    }
  }

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
    announce(`파일 ${accepted.length}개 업로드를 시작했습니다.`)
    try {
      const uploaded = await uploadFiles(accepted, setSent)
      setResults((prev) => [...uploaded, ...prev])
      // 누적 목록이 아니라 이번 요청의 결과만 센다. 일부 실패도 일반 상태로 전한다 (즉시 대응이 필요한 전체 실패만 alert).
      // 실패 사유도 같이 읽는다 — 카드에만 보이면 화면 읽기 사용자는 건수만 듣는다 (서버 문구 그대로, 같은 사유는 한 번)
      const failedNow = uploaded.filter((r) => r.error).length
      const reasons = [...new Set(uploaded.flatMap((r) => (r.error ? [r.error] : [])))]
      const why = reasons.length ? ` 실패 사유: ${reasons.slice(0, 3).join(' / ')}${reasons.length > 3 ? ` 외 ${reasons.length - 3}가지` : ''}` : ''
      announce(`업로드를 마쳤습니다: 이번 요청 ${uploaded.length}개 중 성공 ${uploaded.length - failedNow}개, 실패 ${failedNow}개.${why}`)
      onUploaded?.()
    } catch (err) {
      announce('') // 요청 전체 실패는 아래 오류 알림(alert)이 전한다 — 같은 내용을 두 번 읽지 않게 상태는 비운다
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

  // 업로드 중에는 파일 선택 창을 열지 않는다 (열어도 고른 파일은 처리되지 않음) — 누르면 이유를 알린다
  function openPicker() {
    if (busy) {
      announce('업로드·분석이 끝난 뒤 파일을 고를 수 있습니다.')
      return
    }
    inputRef.current?.click()
  }

  const okIds = results.filter((r) => r.id !== null && !r.error).map((r) => r.id as number)
  const failed = results.filter((r) => r.error).length

  return (
    <section className="upload wk-upload">
      <h2>파일 업로드</h2>
      <p className="wk-card-desc">올린 원본은 바꾸지 않고 읽기만 합니다. 통합 결과는 언제나 새 파일로 만듭니다.</p>
      <p className="sr-only" role="status">
        {announcement}
      </p>
      {/* 조작 요소는 안의 '파일 선택' 버튼 하나다 (이름 = 보이는 글자, 사용 방법·형식은 설명으로 연결).
          둘레 영역은 끌어놓기와 마우스·터치로 아무 곳이나 눌러 여는 편의만 맡는다 — 키보드 초점은 받지 않는다 */}
      <div
        className={`upload-drop${dragging ? ' is-dragging' : ''}${busy ? ' is-busy' : ''}`}
        onClick={(e) => {
          if (e.target !== inputRef.current) openPicker()
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
          <p className="upload-drop-title" id={titleId}>
            {busy ? '업로드·분석 중…' : '파일을 여기에 끌어다 놓으세요'}
          </p>
          {/* 업로드 중에는 '눌러서 선택' 안내가 맞지 않고, 흐리게 그린 영역에서 글자 대비도 모자라 숨긴다 (이유는 영역 아래) */}
          {!busy && (
            <p className="upload-drop-hint" id={hintId}>
              또는 눌러서 선택 · xlsx, xls, csv · 여러 개 가능
              {limits &&
                ` · 파일당 최대 ${limits.max_upload_mib}MiB (xlsx는 풀었을 때 ${limits.max_xlsx_uncompressed_mib}MiB, ` +
                  `${limits.max_file_cells.toLocaleString('ko-KR')}칸까지)`}
            </p>
          )}
        </div>
        {/* 업로드 중에도 그대로 두고 aria-disabled로만 막는다 — disabled로 바꾸면 누른 직후 키보드 초점이 사라진다 */}
        <button
          type="button"
          className="wk-drop-button"
          aria-disabled={busy || undefined}
          aria-describedby={busy ? `${titleId} ${busyId}` : `${titleId} ${hintId}`}
          onClick={(e) => {
            e.stopPropagation() // 둘레 영역의 클릭으로 한 번 더 열지 않게
            openPicker()
          }}
        >
          파일 선택
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(',')}
          hidden
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </div>
      {/* 업로드 중 '파일 선택'이 열리지 않는 이유 — 흐리게 그린 영역 밖, 버튼 바로 아래에 두고 버튼 설명으로 잇는다 */}
      {busy && (
        <p className="wk-why" id={busyId}>
          <strong>지금은 고를 수 없음</strong> 업로드·분석이 끝나면 다시 고를 수 있습니다. 지금 고른 파일은 처리되지
          않습니다.
        </p>
      )}

      {busy && (
        <>
          <ProgressPanel
            live={false}
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
