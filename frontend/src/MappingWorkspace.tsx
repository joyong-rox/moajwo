import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import {
  fetchLatestMapping,
  fetchPreview,
  listFiles,
  mapFile,
  saveDecisions,
  type BatchTarget,
  type ColumnMapping,
  type MappingStatus,
  type SheetPreview,
  type StandardFieldInfo,
  type StoredFile,
  type StoredMapping,
} from './api'
import BatchConfirm from './BatchConfirm'
import FieldPicker from './FieldPicker'
import {
  formatCell,
  loadFields,
  METHOD_LABEL,
  NONE,
  NONE_HELP,
  SCORE_HELP,
  STATUS_HELP,
  STATUS_TEXT,
} from './mappingText'
import { ProgressPanel } from './Progress'
import { MAPPING_STAGES } from './progressStages'
import { navigate } from './router'
import { formatTime } from './runFormat'

const PAGE = 50
const NARROW = '(max-width: 1100px)'

function useNarrow(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(NARROW)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => window.matchMedia(NARROW).matches,
    () => false,
  )
}

type Filter = 'all' | MappingStatus

function readParams() {
  const q = new URLSearchParams(window.location.search)
  const file = Number(q.get('file'))
  return { file: Number.isFinite(file) && file > 0 ? file : null, sheet: q.get('sheet'), col: q.get('col') }
}

/** 주소에 파일·시트·열을 남겨 새로고침·직접 접근에도 같은 열을 연다 (뒤로가기 기록은 만들지 않음) */
function writeParams(file: number | null, sheet: string | null, col: string | null) {
  const q = new URLSearchParams()
  if (file) q.set('file', String(file))
  if (sheet) q.set('sheet', sheet)
  if (col) q.set('col', col)
  const next = `${window.location.pathname}${q.toString() ? `?${q.toString()}` : ''}`
  if (next !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, '', next)
}

const statusClass = (s: MappingStatus) => `mapping-${s}`

function initialChoice(c: ColumnMapping | undefined): string {
  if (!c) return ''
  if (c.status === 'confirmed' && c.field_key === null) return NONE
  return c.field_key ?? ''
}

/** 단일 파일 열 매핑: 상단(파일·시트·현황·검색·필터) / 엑셀형 미리보기 / 선택한 열 상세 */
function MappingWorkspace() {
  const narrow = useNarrow()
  const [initial] = useState(readParams)
  const [files, setFiles] = useState<StoredFile[] | null>(null)
  const [fileId, setFileId] = useState<number | null>(initial.file)
  const [sheet, setSheet] = useState<string | null>(initial.sheet)
  const [selected, setSelected] = useState<string | null>(initial.col)
  const [analysis, setAnalysis] = useState<StoredMapping | null | undefined>(undefined)
  const [preview, setPreview] = useState<SheetPreview | null>(null)
  const [offset, setOffset] = useState(0)
  const [fields, setFields] = useState<StandardFieldInfo[]>([])
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [draft, setDraft] = useState<{ col: string; value: string } | null>(null)
  const [busy, setBusy] = useState<'analyze' | 'save' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [batch, setBatch] = useState<BatchTarget[] | null>(null)
  const [showGrid, setShowGrid] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    listFiles()
      .then(setFiles)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    loadFields()
      .then(setFields)
      .catch(() => undefined)
  }, [])

  // 저장된 분석 (없으면 null — 자동으로 분석하지 않는다)
  useEffect(() => {
    if (fileId === null) return
    let alive = true
    fetchLatestMapping(fileId)
      .then((a) => alive && setAnalysis(a))
      .catch((err: unknown) => {
        if (!alive) return
        setAnalysis(null)
        setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      alive = false
    }
  }, [fileId, reload])

  // 미리보기 (시트·쪽 단위, 최대 PAGE행)
  useEffect(() => {
    if (fileId === null) return
    const controller = new AbortController()
    fetchPreview(fileId, sheet, offset, PAGE, controller.signal)
      .then(setPreview)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
      })
    return () => controller.abort()
  }, [fileId, sheet, offset, reload])

  const sheetName = preview?.sheet ?? sheet
  const sheetMapping = analysis?.sheets.find((s) => s.name === sheetName) ?? null
  // 미리보기와 분석의 열 구성이 같을 때만 열을 연결한다 (다르면 선택·확정 막음)
  const aligned = Boolean(
    preview &&
    sheetMapping &&
    preview.columns.length === sheetMapping.columns.length &&
    preview.columns.every(
      (c, i) => sheetMapping.columns[i]?.column_id === c.column_id && sheetMapping.columns[i]?.column === c.name,
    ),
  )
  const byId = useMemo(() => new Map((sheetMapping?.columns ?? []).map((c) => [c.column_id, c])), [sheetMapping])
  const selectedCol = aligned && selected ? byId.get(selected) : undefined
  const selectedPreview = preview?.columns.find((c) => c.column_id === selected)
  const canChange = Boolean(analysis?.can_change) && aligned && !analysis?.stale

  useEffect(() => {
    writeParams(fileId, sheetName, selectedCol ? selected : null)
  }, [fileId, sheetName, selected, selectedCol])

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: sheetMapping?.columns.length ?? 0 }
    for (const col of sheetMapping?.columns ?? []) c[col.status] = (c[col.status] ?? 0) + 1
    return c
  }, [sheetMapping])

  const visible = useCallback(
    (c: ColumnMapping | undefined, name: string) => {
      if (filter !== 'all' && c?.status !== filter) return false
      if (!query.trim()) return true
      const q = query.replace(/\s+/g, '').toLowerCase()
      return [name, c?.field_label ?? ''].some((s) => s.replace(/\s+/g, '').toLowerCase().includes(q))
    },
    [filter, query],
  )
  const shownColumns = (preview?.columns ?? []).filter((pc) => visible(byId.get(pc.column_id), pc.name))
  const hidden = (preview?.columns.length ?? 0) - shownColumns.length

  function pickFile(id: number | null) {
    setFileId(id)
    setSheet(null)
    setSelected(null)
    setOffset(0)
    setPreview(null)
    setAnalysis(undefined)
    setDraft(null)
    setBatch(null)
    setMessage(null)
    setError(null)
  }

  function pickSheet(name: string) {
    setSheet(name)
    setSelected(null)
    setOffset(0)
    setDraft(null)
    setBatch(null)
  }

  function pickColumn(id: string) {
    setSelected(id)
    setDraft(null)
    setMessage(null)
  }

  async function analyze() {
    if (fileId === null || busy) return
    setBusy('analyze')
    setError(null)
    setMessage(null)
    try {
      await mapFile(fileId)
      const fresh = await fetchLatestMapping(fileId)
      setAnalysis(fresh)
      setDraft(null)
      setReload((v) => v + 1)
      setMessage('열 매핑을 분석했습니다. 자동 제안은 확정 전입니다.')
    } catch (err) {
      setError(`분석 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(null)
    }
  }

  const choice = selectedCol ? (draft?.col === selectedCol.column_id ? draft.value : initialChoice(selectedCol)) : ''

  async function confirm(value: string) {
    if (!selectedCol || fileId === null || !sheetName || busy || value === '') return
    setBusy('save')
    setError(null)
    try {
      await saveDecisions(fileId, [
        { sheet: sheetName, column_id: selectedCol.column_id, field_key: value === NONE ? null : value },
      ])
      const fresh = await fetchLatestMapping(fileId)
      setAnalysis(fresh)
      setDraft(null)
      const label = value === NONE ? '해당 없음' : (fields.find((f) => f.key === value)?.label ?? value)
      setMessage(`'${selectedCol.column}' 열을 '${label}'(으)로 확정했습니다.`)
    } catch (err) {
      setError(`저장 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(null)
    }
  }

  const suggestions = (sheetMapping?.columns ?? []).filter((c) => c.status !== 'confirmed' && c.field_key !== null)

  const detail = selectedCol && selectedPreview && (
    <aside className="mw-detail" aria-label="선택한 열">
      {narrow && (
        <button type="button" className="mapping-confirm" onClick={() => setSelected(null)}>
          ← 열 목록으로
        </button>
      )}
      <h3>
        <span className="mw-letter">{selectedPreview.letter}</span> {selectedCol.column}
      </h3>
      <p>
        <span className={`upload-tag ${statusClass(selectedCol.status)}`}>{STATUS_TEXT[selectedCol.status]}</span>{' '}
        {selectedCol.field_label ?? (selectedCol.status === 'confirmed' ? '해당 없음' : '연결 없음')}
        {selectedCol.qualifier && <span className="upload-tag">{selectedCol.qualifier}</span>}
      </p>
      <p className="mw-muted">{STATUS_HELP[selectedCol.status]}</p>
      <div>
        <h4>값 예시</h4>
        {selectedCol.samples.length ? (
          <ul className="mw-samples">
            {selectedCol.samples.map((s, i) => (
              <li key={i}>{formatCell(s)}</li>
            ))}
          </ul>
        ) : (
          <p className="mw-muted">값이 없습니다.</p>
        )}
      </div>
      {selectedCol.candidates.length > 0 && (
        <div>
          <h4>추천 후보</h4>
          <ul className="mw-candidates">
            {selectedCol.candidates.map((c) => (
              <li key={c.key}>
                {c.label} <span className="mw-muted">유사도 참고값 {c.score.toFixed(2)}</span>
              </li>
            ))}
          </ul>
          <p className="mw-muted">{SCORE_HELP}</p>
        </div>
      )}
      {selectedCol.reason && (
        <details className="mw-reason">
          <summary>분석 근거 보기 ({METHOD_LABEL[selectedCol.method]})</summary>
          <p>{selectedCol.reason}</p>
        </details>
      )}
      {!canChange && (
        <p className="mapping-warn" role="note">
          {analysis?.can_change === false
            ? '조회 전용입니다 — 파일을 올린 사용자만 확정할 수 있습니다.'
            : '열 구성이 분석 때와 달라 확정할 수 없습니다. 다시 분석하세요.'}
        </p>
      )}
      <FieldPicker
        fields={fields}
        value={choice}
        disabled={!canChange || busy !== null}
        suggested={selectedCol.candidates.map((c) => c.key)}
        onChange={(value) => setDraft({ col: selectedCol.column_id, value })}
      />
      <p className="mw-muted">{NONE_HELP}</p>
      <div className="admin-actions">
        <button
          type="button"
          className="mapping-run"
          disabled={
            !canChange ||
            busy !== null ||
            choice === '' ||
            (selectedCol.status === 'confirmed' && choice === initialChoice(selectedCol))
          }
          onClick={() => void confirm(choice)}
        >
          {busy === 'save' ? '저장 중…' : selectedCol.status === 'confirmed' ? '이 값으로 변경 확정' : '이 값으로 확정'}
        </button>
        {draft?.col === selectedCol.column_id && (
          <button type="button" className="mapping-confirm" onClick={() => setDraft(null)} disabled={busy !== null}>
            선택 취소
          </button>
        )}
      </div>
    </aside>
  )

  const grid = preview && (
    <div className="mw-grid-wrap" role="region" aria-label="데이터 미리보기 (파싱 후 정리된 값)" tabIndex={0}>
      <table className="mw-grid">
        <thead>
          <tr>
            <th scope="col" className="mw-rownum">
              행
            </th>
            {shownColumns.map((pc) => {
              const m = byId.get(pc.column_id)
              const active = pc.column_id === selected
              return (
                <th key={pc.column_id} scope="col" className={active ? 'is-selected' : undefined}>
                  <button
                    type="button"
                    className="mw-colhead"
                    aria-pressed={active}
                    aria-label={`${pc.letter}열 ${pc.name} 선택`}
                    disabled={!aligned}
                    onClick={() => pickColumn(pc.column_id)}
                  >
                    <span className="mw-letter">{pc.letter}</span>
                    <span className="mw-colname">{pc.name}</span>
                    {m && (
                      <span className={`upload-tag ${statusClass(m.status)}`}>
                        {m.field_label ?? (m.status === 'confirmed' ? '해당 없음' : '미매핑')} · {STATUS_TEXT[m.status]}
                      </span>
                    )}
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((r) => (
            <tr key={r.row} className={`mw-row-${r.kind}`}>
              <th scope="row" className="mw-rownum">
                {r.row}
              </th>
              {shownColumns.map((pc) => {
                const index = preview.columns.indexOf(pc)
                return (
                  <td key={pc.column_id} className={pc.column_id === selected ? 'is-selected' : undefined}>
                    {formatCell(r.cells[index] ?? null)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  return (
    <section className="mw" aria-label="열 매핑 작업">
      <div className="mw-top">
        <label>
          파일
          <select
            value={fileId ?? ''}
            onChange={(e) => pickFile(e.target.value ? Number(e.target.value) : null)}
            aria-label="파일 선택"
          >
            <option value="">— 파일 선택 —</option>
            {(files ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                #{f.id} {f.original_name}
              </option>
            ))}
          </select>
        </label>
        {preview && preview.sheets.length > 1 && (
          <label>
            시트
            <select value={sheetName ?? ''} onChange={(e) => pickSheet(e.target.value)} aria-label="시트 선택">
              {preview.sheets.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
        {fileId !== null && (
          <button type="button" className="mapping-run" onClick={() => void analyze()} disabled={busy !== null}>
            {busy === 'analyze' ? '분석 중… (LLM 사용 시 수십 초)' : analysis ? '열 매핑 다시 분석' : '열 매핑 분석'}
          </button>
        )}
        <a
          className="mapping-confirm mw-link"
          href="/mapping/common"
          onClick={(e) => {
            e.preventDefault()
            navigate('/mapping/common')
          }}
        >
          여러 파일 공통 검토
        </a>
      </div>
      {busy === 'analyze' && <ProgressPanel stages={MAPPING_STAGES} />}
      {message && (
        <p className="mapping-message" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {fileId === null && <p className="upload-drop-hint">열 매핑을 볼 파일을 고르세요.</p>}
      {fileId !== null && preview === null && !error && (
        <p className="mw-muted" role="status">
          미리보기를 불러오는 중… 큰 파일(수만 행)은 처음 열 때 수십 초 걸릴 수 있습니다. 같은 파일의 다음 쪽부터는 바로
          열립니다.
        </p>
      )}
      {fileId !== null && analysis === null && busy !== 'analyze' && (
        <p className="mapping-warn" role="status">
          이 파일의 저장된 분석이 없습니다. '열 매핑 분석'을 누르면 분석합니다 (자동으로 분석하지 않음).
        </p>
      )}
      {analysis && (
        <p className="mw-muted">
          분석 {formatTime(analysis.analyzed_at)}
          {analysis.stale && <strong className="mw-stale"> · 파일 구성이 분석 때와 달라 다시 분석이 필요합니다</strong>}
          {!analysis.can_change && ' · 조회 전용 (다른 사용자의 파일)'}
        </p>
      )}
      {preview && (
        <p className="mw-basis">
          {preview.basis} 머리글 {preview.header_row ? `${preview.header_row}~${preview.header_row_end}행` : '없음'} ·
          데이터 {preview.data_rows.toLocaleString('ko-KR')}행
          {preview.dropped_columns > 0 && ` · 병합 복제 열 ${preview.dropped_columns}개 제외`}
        </p>
      )}
      {sheetMapping && (
        <div className="mw-tools">
          <div className="admin-filters" role="group" aria-label="상태 필터">
            {(['all', 'confirmed', 'auto', 'review', 'unresolved'] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                className={`admin-filter${filter === f ? ' is-active' : ''}`}
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? '전체' : STATUS_TEXT[f]} {counts[f] ?? 0}
              </button>
            ))}
          </div>
          <input
            type="search"
            className="mw-search"
            placeholder="열 이름·표준 항목 검색"
            aria-label="열 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button
            type="button"
            className="mapping-confirm"
            disabled={!canChange || suggestions.length === 0 || busy !== null}
            onClick={() =>
              setBatch(
                suggestions.map((c) => ({
                  file_id: fileId as number,
                  sheet: sheetName as string,
                  column_id: c.column_id,
                  field_key: c.field_key,
                })),
              )
            }
          >
            제안대로 확정 준비 ({suggestions.length})
          </button>
        </div>
      )}
      {batch && (
        <BatchConfirm
          targets={batch}
          title={`'${sheetName}' 시트의 자동 제안 확정`}
          onCancel={() => setBatch(null)}
          onDone={(text) => {
            setBatch(null)
            setMessage(text)
            setReload((v) => v + 1)
          }}
        />
      )}
      {preview && sheetMapping && !aligned && (
        <p className="mapping-warn" role="alert">
          미리보기와 분석의 열 구성이 달라 열을 연결하지 않았습니다. 다시 분석하세요.
        </p>
      )}
      {preview && hidden > 0 && (
        <p className="mw-muted">
          필터로 {hidden}개 열을 숨겼습니다.{' '}
          <button
            type="button"
            className="login-link"
            onClick={() => {
              setFilter('all')
              setQuery('')
            }}
          >
            필터 해제
          </button>
        </p>
      )}
      {preview && narrow && (
        <div className="mw-mobile">
          {!selectedCol && (
            <ul className="mw-collist" aria-label="열 목록">
              {shownColumns.map((pc) => {
                const m = byId.get(pc.column_id)
                return (
                  <li key={pc.column_id}>
                    <button
                      type="button"
                      className="mw-colitem"
                      disabled={!aligned}
                      onClick={() => pickColumn(pc.column_id)}
                    >
                      <span className="mw-letter">{pc.letter}</span>
                      <span className="mw-colname">{pc.name}</span>
                      {m && (
                        <span className={`upload-tag ${statusClass(m.status)}`}>
                          {m.field_label ?? (m.status === 'confirmed' ? '해당 없음' : '미매핑')} ·{' '}
                          {STATUS_TEXT[m.status]}
                        </span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {detail}
          <button
            type="button"
            className="mapping-confirm"
            onClick={() => setShowGrid((v) => !v)}
            aria-expanded={showGrid}
          >
            {showGrid ? '데이터 미리보기 닫기' : '데이터 미리보기 열기'}
          </button>
          {showGrid && grid}
        </div>
      )}
      {preview && !narrow && (
        <div className={`mw-main${detail ? ' has-detail' : ''}`}>
          {grid}
          {detail || (
            <aside className="mw-detail mw-empty" aria-label="선택한 열">
              <p className="mw-muted">
                미리보기의 열 머리글을 누르면 그 열의 값 예시·후보·근거를 보고 확정할 수 있습니다.
              </p>
            </aside>
          )}
        </div>
      )}
      {preview && (
        <div className="mw-pager">
          <button
            type="button"
            className="mapping-confirm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            이전 {PAGE}행
          </button>
          <span className="mw-muted">
            {preview.total_rows === 0
              ? '행 없음'
              : `${preview.rows[0]?.row ?? '-'}~${preview.rows[preview.rows.length - 1]?.row ?? '-'}행 (${offset + 1}~${offset + preview.rows.length}번째 / 전체 ${preview.total_rows.toLocaleString('ko-KR')})`}
          </span>
          <button
            type="button"
            className="mapping-confirm"
            disabled={offset + PAGE >= preview.total_rows}
            onClick={() => setOffset(offset + PAGE)}
          >
            다음 {PAGE}행
          </button>
        </div>
      )}
    </section>
  )
}

export default MappingWorkspace
