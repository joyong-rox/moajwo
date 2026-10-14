import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import {
  applyBatch,
  BatchConflictError,
  fetchLatestMapping,
  fetchPreview,
  listFiles,
  mapFile,
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
  const sheet = q.get('sheet')
  return {
    file: Number.isFinite(file) && file > 0 ? file : null,
    sheet: sheet && /^s\d+$/.test(sheet) ? sheet : null,
    col: q.get('col'),
  }
}

/** 주소에 파일·시트(sheet_id)·열을 남겨 새로고침·직접 접근에도 같은 열을 연다 (뒤로가기 기록은 만들지 않음) */
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
  return c.status === 'unanalyzed' ? '' : (c.field_key ?? '')
}

/** 단일 파일 열 매핑: 상단(파일·시트·현황·검색·필터) / 엑셀형 미리보기 / 선택한 열 상세 */
function MappingWorkspace() {
  const narrow = useNarrow()
  const [initial] = useState(readParams)
  const [files, setFiles] = useState<StoredFile[] | null>(null)
  const [fileId, setFileId] = useState<number | null>(initial.file)
  const [sheet, setSheet] = useState<string | null>(initial.sheet) // sheet_id (s0, s1 …)
  const [selected, setSelected] = useState<string | null>(initial.col)
  const [analysis, setAnalysis] = useState<StoredMapping | undefined>(undefined)
  const [preview, setPreview] = useState<SheetPreview | null>(null)
  const [offset, setOffset] = useState(0)
  const [fields, setFields] = useState<StandardFieldInfo[]>([])
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [draft, setDraft] = useState<{ col: string; value: string } | null>(null)
  const [busy, setBusy] = useState<'analyze' | 'save' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [batch, setBatch] = useState<{ targets: BatchTarget[]; note?: string } | null>(null)
  const [showGrid, setShowGrid] = useState(false)
  const [reload, setReload] = useState(0)
  // 지금 화면이 가리키는 파일 — 늦게 도착한 다른 파일의 응답을 화면에 반영하지 않으려고 비교한다
  const currentFile = useRef<number | null>(fileId)
  useEffect(() => {
    currentFile.current = fileId
  }, [fileId])

  useEffect(() => {
    listFiles()
      .then(setFiles)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    loadFields()
      .then(setFields)
      .catch(() => undefined)
  }, [])

  // 작업 화면 매핑: 저장된 분석(있으면) + 확정값. 분석이 없으면 '미분석'으로 받고 자동으로 분석하지 않는다
  useEffect(() => {
    if (fileId === null) return
    let alive = true
    fetchLatestMapping(fileId)
      .then((a) => {
        if (alive && a.file_id === fileId) setAnalysis(a)
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err))
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
      .then((p) => {
        if (!controller.signal.aborted && p.file_id === fileId) setPreview(p)
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
      })
    return () => controller.abort()
  }, [fileId, sheet, offset, reload])

  // 지금 선택한 파일·시트의 미리보기·매핑일 때만 쓴다 (전환 중 이전 파일 값이 남아 있어도 연결하지 않음)
  const view = preview && preview.file_id === fileId && (sheet === null || preview.sheet_id === sheet) ? preview : null
  const mapping = analysis && analysis.file_id === fileId ? analysis : undefined
  const sheetId = view?.sheet_id ?? sheet
  const sheetMapping = mapping?.sheets.find((s) => s.sheet_id === sheetId) ?? null
  // 미리보기와 매핑의 파일·시트·열 구성이 같을 때만 열을 연결한다 (다르면 선택·확정 막음)
  const aligned = Boolean(
    view &&
    sheetMapping &&
    view.columns.length === sheetMapping.columns.length &&
    view.columns.every(
      (c, i) => sheetMapping.columns[i]?.column_id === c.column_id && sheetMapping.columns[i]?.column === c.name,
    ),
  )
  const byId = useMemo(() => new Map((sheetMapping?.columns ?? []).map((c) => [c.column_id, c])), [sheetMapping])
  const selectedCol = aligned && selected ? byId.get(selected) : undefined
  const selectedPreview = view?.columns.find((c) => c.column_id === selected)
  const canChange = Boolean(mapping?.can_change) && aligned && !mapping?.stale

  useEffect(() => {
    writeParams(fileId, sheetId, selectedCol ? selected : null)
  }, [fileId, sheetId, selected, selectedCol])

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
  const shownColumns = (view?.columns ?? []).filter((pc) => visible(byId.get(pc.column_id), pc.name))
  const hidden = (view?.columns.length ?? 0) - shownColumns.length

  function pickFile(id: number | null) {
    currentFile.current = id
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

  function pickSheet(id: string) {
    setSheet(id)
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
    const target = fileId
    setBusy('analyze')
    setError(null)
    setMessage(null)
    try {
      await mapFile(target)
      const fresh = await fetchLatestMapping(target)
      if (currentFile.current !== target) return // 그 사이 다른 파일로 바꿨으면 화면에 반영하지 않는다
      setAnalysis(fresh)
      setDraft(null)
      setReload((v) => v + 1)
      setMessage('열 매핑을 분석했습니다. 자동 제안은 확정 전입니다.')
    } catch (err) {
      if (currentFile.current === target) setError(`분석 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(null)
    }
  }

  const choice = selectedCol ? (draft?.col === selectedCol.column_id ? draft.value : initialChoice(selectedCol)) : ''

  /** 한 열 확정: 지금 보고 있는 확정값을 기대값으로 보내, 그 사이 다른 요청(다른 탭·일괄 확정)이 바꿨으면 덮어쓰지 않는다 */
  async function confirm(value: string) {
    if (!selectedCol || fileId === null || !sheetId || busy || value === '') return
    const target = { fileId, sheetId, col: selectedCol }
    setBusy('save')
    setError(null)
    try {
      const confirmed = target.col.status === 'confirmed'
      await applyBatch([
        {
          file_id: target.fileId,
          sheet_id: target.sheetId,
          column_id: target.col.column_id,
          field_key: value === NONE ? null : value,
          expected_confirmed: confirmed,
          expected_field_key: confirmed ? target.col.field_key : null,
          overwrite: confirmed, // 확정된 열을 바꾸는 것은 사용자가 이 화면에서 직접 고른 것
        },
      ])
      const fresh = await fetchLatestMapping(target.fileId)
      if (currentFile.current !== target.fileId) return // 저장은 원래 파일에 됨, 화면은 이미 다른 파일
      setAnalysis(fresh)
      setDraft(null)
      const label = value === NONE ? '해당 없음' : (fields.find((f) => f.key === value)?.label ?? value)
      setMessage(`'${target.col.column}' 열을 '${label}'(으)로 확정했습니다.`)
    } catch (err) {
      if (currentFile.current !== target.fileId) return
      if (err instanceof BatchConflictError) {
        setError(`저장하지 않았습니다: ${err.problems.join(' / ') || err.message} — 최신 상태로 다시 불러왔습니다.`)
        setReload((v) => v + 1)
      } else {
        setError(`저장 실패: ${err instanceof Error ? err.message : String(err)}`)
      }
    } finally {
      setBusy(null)
    }
  }

  // 일괄 확정은 '자동 제안'만 기본 대상 — '확인 필요'는 열마다 직접 확인해 확정한다
  const suggestions = (sheetMapping?.columns ?? []).filter((c) => c.status === 'auto' && c.field_key !== null)
  const hiddenSuggestions = suggestions.filter((c) => !shownColumns.some((pc) => pc.column_id === c.column_id)).length
  const reviewCount = (sheetMapping?.columns ?? []).filter((c) => c.status === 'review').length

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
          {mapping?.can_change === false
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

  const grid = view && (
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
          {view.rows.map((r) => (
            <tr key={r.row} className={`mw-row-${r.kind}`}>
              <th scope="row" className="mw-rownum">
                {r.row}
              </th>
              {shownColumns.map((pc) => {
                const index = view.columns.indexOf(pc)
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
                #{f.id} {f.display_name}
              </option>
            ))}
          </select>
        </label>
        {view && view.sheets.length > 1 && (
          <label>
            시트
            <select value={sheetId ?? ''} onChange={(e) => pickSheet(e.target.value)} aria-label="시트 선택">
              {view.sheets.map((s) => (
                <option key={s.sheet_id} value={s.sheet_id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {fileId !== null && (
          <button type="button" className="mapping-run" onClick={() => void analyze()} disabled={busy !== null}>
            {busy === 'analyze'
              ? '분석 중… (LLM 사용 시 수십 초)'
              : mapping?.analyzed
                ? '열 매핑 다시 분석'
                : '열 매핑 분석'}
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
      {fileId !== null && view === null && !error && (
        <p className="mw-muted" role="status">
          미리보기를 불러오는 중… 큰 파일(수만 행)은 처음 열 때 수십 초 걸릴 수 있습니다. 같은 파일의 다음 쪽부터는 바로
          열립니다.
        </p>
      )}
      {mapping && !mapping.analyzed && busy !== 'analyze' && (
        <p className="mapping-warn" role="status">
          이 파일의 저장된 분석이 없습니다. 저장된 확정값은 '사용자 확정'으로, 나머지 열은 '미분석'으로 보입니다. '열
          매핑 분석'을 눌러야 분석합니다 (자동으로 분석하지 않음).
        </p>
      )}
      {mapping?.analyzed && (
        <p className="mw-muted">
          분석 {formatTime(mapping.analyzed_at)}
          {mapping.stale && <strong className="mw-stale"> · 파일 구성이 분석 때와 달라 다시 분석이 필요합니다</strong>}
          {!mapping.can_change && ' · 조회 전용 (다른 사용자의 파일)'}
        </p>
      )}
      {mapping && !mapping.analyzed && !mapping.can_change && (
        <p className="mw-muted">조회 전용 (다른 사용자의 파일)</p>
      )}
      {view && (
        <p className="mw-basis">
          {view.basis} 머리글 {view.header_row ? `${view.header_row}~${view.header_row_end}행` : '없음'} · 데이터{' '}
          {view.data_rows.toLocaleString('ko-KR')}행
          {view.dropped_columns > 0 && ` · 병합 복제 열 ${view.dropped_columns}개 제외`}
        </p>
      )}
      {sheetMapping && (
        <div className="mw-tools">
          <div className="admin-filters" role="group" aria-label="상태 필터">
            {(['all', 'confirmed', 'auto', 'review', 'unresolved', 'unanalyzed'] as Filter[]).map((f) => (
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
              setBatch({
                targets: suggestions.map((c) => ({
                  file_id: fileId as number,
                  sheet_id: sheetId as string,
                  column_id: c.column_id,
                  field_key: c.field_key,
                })),
                note:
                  (hiddenSuggestions > 0
                    ? `지금 필터·검색으로 화면에 보이지 않는 자동 제안 ${hiddenSuggestions}개도 포함됩니다. `
                    : '') +
                  `이 시트의 '자동 제안' 열만 대상입니다. '확인 필요' ${reviewCount}개는 포함하지 않으며, 열을 골라 직접 확인·확정하세요. 확인 표에서 열마다 뺄 수 있습니다.`,
              })
            }
          >
            자동 제안 일괄 확정 준비 ({suggestions.length})
          </button>
        </div>
      )}
      {batch && (
        <BatchConfirm
          targets={batch.targets}
          note={batch.note}
          title={`'${view?.sheet ?? ''}' 시트의 자동 제안 일괄 확정 확인`}
          onCancel={() => setBatch(null)}
          onDone={(text) => {
            setBatch(null)
            setMessage(text)
            setReload((v) => v + 1)
          }}
        />
      )}
      {view && sheetMapping && !aligned && (
        <p className="mapping-warn" role="alert">
          미리보기와 분석의 열 구성이 달라 열을 연결하지 않았습니다. 다시 분석하세요.
        </p>
      )}
      {view && hidden > 0 && (
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
      {view && narrow && (
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
      {view && !narrow && (
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
      {view && (
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
            {view.total_rows === 0
              ? '행 없음'
              : `${view.rows[0]?.row ?? '-'}~${view.rows[view.rows.length - 1]?.row ?? '-'}행 (${offset + 1}~${offset + view.rows.length}번째 / 전체 ${view.total_rows.toLocaleString('ko-KR')})`}
          </span>
          <button
            type="button"
            className="mapping-confirm"
            disabled={offset + PAGE >= view.total_rows}
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
