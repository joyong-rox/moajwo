import { useEffect, useState } from 'react'
import { replaceSearch, useSearch } from './router'
import { Banner } from './Workflow'
import PiiNotice from './PiiNotice'
import {
  createAlias,
  listFiles,
  runValidation,
  type EntityNote,
  type StoredFile,
  type ValidationIssue,
  type ValidationRequest,
  type ValidationRun,
  type ValidationStatus,
} from './api'

const STATUS_TEXT: Record<ValidationStatus, string> = {
  blocked: '검토 필요 — 차단 항목이 있어 통합할 수 없습니다',
  pii_confirmation_required: '개인정보 확인 필요 — 확인 후 통합할 수 있습니다',
  passed_with_warnings: '통합 가능 — 경고 항목을 확인하세요',
  passed: '통합 가능 — 문제가 없습니다',
}

const MAX_TARGETS = 50 // 서버의 한 번에 검사하는 파일 수 제한과 같음 (validation_api.MAX_FILES)

const ENTITY_OPTIONS = [
  { value: '', label: '자동 (기관명 → 시군구 → 행정구역 → 시도)' },
  { value: 'org_name', label: '기관명' },
  { value: 'sido', label: '시도' },
  { value: 'sigungu', label: '시군구' },
  { value: 'region', label: '행정구역' },
  { value: 'region_code', label: '행정구역코드' },
]

type ValidationProps = {
  refreshKey: number
  canManageGlossary?: boolean // 용어사전 별칭 등록은 관리자·소유자만
  onGlossaryChanged: () => void
  onOpenReview: (request: ValidationRequest) => void
  onSelection?: (ids: number[]) => void // 검사 대상 선택 (홈 단계 안내)
}

function Validation({ refreshKey, canManageGlossary = true, onGlossaryChanged, onOpenReview, onSelection }: ValidationProps) {
  const [files, setFiles] = useState<StoredFile[]>([])
  const [targets, setTargets] = useState<Set<number>>(new Set())
  const [baseline, setBaseline] = useState<Set<number>>(new Set())
  const [period, setPeriod] = useState('')
  const [entities, setEntities] = useState('')
  const [entityField, setEntityField] = useState('')
  const [threshold, setThreshold] = useState(50)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [run, setRun] = useState<ValidationRun | null>(null)
  const [localVersion, setLocalVersion] = useState(0)
  const [query, setQuery] = useState('')
  const search = useSearch()
  const wanted = window.location.pathname === '/' ? new URLSearchParams(search).get('files') : null

  // 열 매핑 화면의 '다음: 이 파일로 검사·검토' (/?files=번호) — 그 파일을 검사 대상으로 골라 둔다 (실행은 하지 않음)
  const [applied, setApplied] = useState<string | null>(null)
  if (!wanted && applied !== null) setApplied(null)
  if (wanted && files.length > 0 && wanted !== applied) {
    setApplied(wanted)
    const ids = wanted
      .split(',')
      .map(Number)
      .filter((id) => files.some((f) => f.id === id))
      .slice(0, MAX_TARGETS)
    if (ids.length) {
      setTargets(new Set(ids))
      setBaseline((prev) => new Set([...prev].filter((id) => !ids.includes(id))))
    }
  }
  // 반영한 뒤 주소에서 빼고(새로고침해도 다시 고르지 않음) 검사 영역으로 옮긴다
  useEffect(() => {
    if (!wanted || applied !== wanted) return
    document.getElementById('wk-validation')?.scrollIntoView({ block: 'start' })
    const q = new URLSearchParams(window.location.search)
    q.delete('files')
    replaceSearch(q.toString() ? `?${q.toString()}` : '')
  }, [wanted, applied])

  useEffect(() => {
    onSelection?.([...targets])
  }, [targets, onSelection])

  useEffect(() => {
    let cancelled = false
    listFiles()
      .then((list) => {
        if (!cancelled) setFiles(list)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(`파일 목록을 불러오지 못했습니다: ${err instanceof Error ? err.message : String(err)}`)
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey, localVersion])

  function toggle(set: Set<number>, id: number, on: boolean) {
    const next = new Set(set)
    if (on) next.add(id)
    else next.delete(id)
    return next
  }

  function buildRequest(): ValidationRequest {
    return {
      file_ids: [...targets],
      baseline_file_ids: [...baseline],
      expected_period: period.trim() || null,
      required_entities: entities
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean),
      entity_field: entityField || null,
      change_threshold: threshold / 100,
    }
  }

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      setRun(await runValidation(buildRequest()))
    } catch (err) {
      setError(`검증 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
  }

  const q = query.replace(/\s+/g, '').toLowerCase()
  const shownFiles = q
    ? files.filter((f) => `#${f.id}${f.display_name}`.replace(/\s+/g, '').toLowerCase().includes(q))
    : files
  const hiddenPicked = [...targets, ...baseline].filter((id) => !shownFiles.some((f) => f.id === id)).length
  const full = targets.size >= MAX_TARGETS

  return (
    <section className="validation wk-validation" id="wk-validation">
      <h2>통합 전 검증 · 검토</h2>
      {files.length === 0 ? (
        <Banner tone="info" icon="file" title="아직 올린 파일이 없습니다">
          <p>위의 '파일 업로드'에서 파일을 올리면 여기서 검사할 파일을 고를 수 있습니다.</p>
        </Banner>
      ) : (
        <>
          <div className="wk-pickbar">
            <input
              type="search"
              className="mw-search"
              placeholder="파일 번호·이름 검색"
              aria-label="검사할 파일 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <span className="wk-pickcount" role="status">
              검증 대상 <strong>{targets.size}</strong>개 (최대 {MAX_TARGETS}) · 전월 자료 <strong>{baseline.size}</strong>개 · 전체{' '}
              {files.length}개
            </span>
            {(targets.size > 0 || baseline.size > 0) && (
              <button
                type="button"
                className="login-link"
                onClick={() => {
                  setTargets(new Set())
                  setBaseline(new Set())
                }}
              >
                선택 모두 해제
              </button>
            )}
          </div>
          {hiddenPicked > 0 && (
            <p className="mapping-warn" role="status">
              고른 파일 중 {hiddenPicked}개가 검색으로 숨겨져 있습니다 — 숨겨져도 검사 대상에 그대로 들어갑니다.{' '}
              <button type="button" className="login-link" onClick={() => setQuery('')}>
                검색 지우기
              </button>
            </p>
          )}
          {full && <p className="mw-muted">한 번에 {MAX_TARGETS}개까지 검사할 수 있습니다.</p>}
        <div className="upload-table-wrap wk-filetable">
          <table className="upload-table validation-files">
            <thead>
              <tr>
                <th scope="col">검증 대상</th>
                <th scope="col">전월 자료</th>
                <th scope="col">파일</th>
                <th scope="col">개인정보</th>
              </tr>
            </thead>
            <tbody>
              {shownFiles.map((f) => (
                <tr key={f.id} className={targets.has(f.id) ? 'is-picked' : undefined}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`#${f.id} ${f.display_name} 검증 대상`}
                      checked={targets.has(f.id)}
                      disabled={!targets.has(f.id) && full}
                      onChange={(e) => {
                        setTargets(toggle(targets, f.id, e.target.checked))
                        if (e.target.checked) setBaseline(toggle(baseline, f.id, false))
                      }}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`#${f.id} ${f.display_name} 전월 자료`}
                      checked={baseline.has(f.id)}
                      onChange={(e) => {
                        setBaseline(toggle(baseline, f.id, e.target.checked))
                        if (e.target.checked) setTargets(toggle(targets, f.id, false))
                      }}
                    />
                  </td>
                  <td className="wk-filename">
                    #{f.id} {f.display_name}
                  </td>
                  <td>
                    {f.pii ? (
                      <span className={`upload-tag ${f.pii.acknowledged ? 'mapping-confirmed' : 'mapping-unresolved'}`}>
                        {f.pii.acknowledged ? '확인됨' : '확인 필요'}
                      </span>
                    ) : (
                      '-'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}

      <div className="validation-form">
        <label>
          기준 기간
          <input
            type="text"
            inputMode="numeric"
            placeholder="2026-09 또는 2026"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
          />
        </label>
        <label>
          기관·지역 기준 열
          <select value={entityField} onChange={(e) => setEntityField(e.target.value)}>
            {ENTITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          전월 대비 이상치 기준 (±%)
          <input
            type="number"
            min={1}
            max={1000}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value) || 50)}
          />
        </label>
        <label className="validation-wide">
          필수 기관·지역 (쉼표 또는 줄바꿈으로 구분)
          <textarea
            rows={2}
            placeholder="행정안전부, 기상청, 인사혁신처"
            value={entities}
            onChange={(e) => setEntities(e.target.value)}
          />
        </label>
      </div>
      <div className="review-open">
        <button type="button" className="mapping-run" disabled={busy || targets.size === 0} onClick={() => void submit()}>
          {busy ? '검증 중…' : `검증 실행 (${targets.size}개 파일)`}
        </button>
        <button
          type="button"
          className="mapping-run"
          disabled={busy || targets.size === 0}
          onClick={() => onOpenReview(buildRequest())}
        >
          사전 검토 열기 (통합 전)
        </button>
      </div>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {run && (
        <ValidationResult
          run={run}
          onPiiChanged={() => setLocalVersion((v) => v + 1)}
          onGlossaryChanged={onGlossaryChanged}
          canManageGlossary={canManageGlossary}
        />
      )}
    </section>
  )
}

function IssueTable({ issues }: { issues: ValidationIssue[] }) {
  return (
    <div className="upload-table-wrap">
      <table className="upload-table validation-issues">
        <thead>
          <tr>
            <th scope="col">항목</th>
            <th scope="col">위치</th>
            <th scope="col">내용</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((issue, i) => (
            <tr key={i}>
              <th scope="row">{issue.label}</th>
              <td>
                {[issue.file_name, issue.sheet, issue.row ? `${issue.row}행` : null, issue.column]
                  .filter(Boolean)
                  .join(' / ') || '전체'}
              </td>
              <td className="validation-message">{issue.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

type ResultProps = {
  run: ValidationRun
  onPiiChanged: () => void
  onGlossaryChanged: () => void
  canManageGlossary: boolean
}

function ValidationResult({ run, onPiiChanged, onGlossaryChanged, canManageGlossary }: ResultProps) {
  return (
    <div className="validation-result">
      <p className={`validation-status status-${run.status}`} role="status">
        검증 #{run.id}: {STATUS_TEXT[run.status]}
      </p>

      <section className="validation-group group-block">
        <h3>
          차단 {run.blocks.length}건{run.blocks.length > 0 && ' · 통합 불가'}
        </h3>
        {run.blocks.length ? <IssueTable issues={run.blocks} /> : <p className="upload-drop-hint">없음</p>}
      </section>

      <section className="validation-group group-warning">
        <h3>경고 {run.warnings.length}건 · 통합은 진행</h3>
        {run.warnings.length ? <IssueTable issues={run.warnings} /> : <p className="upload-drop-hint">없음</p>}
      </section>

      <section className="validation-group group-pii">
        <h3>
          개인정보 {run.pii.length}개 파일 ·{' '}
          {run.pii.some((p) => !p.acknowledged) ? '사용자 확인 필요' : run.pii.length ? '확인 완료' : '해당 없음'}
        </h3>
        {run.pii.length ? (
          run.pii.map((p) => (
            <div key={p.file_id}>
              <p className="validation-pii-file">{p.file_name}</p>
              <PiiNotice fileId={p.file_id} pii={p} onAcknowledged={onPiiChanged} />
            </div>
          ))
        ) : (
          <p className="upload-drop-hint">발견되지 않음</p>
        )}
        {run.pii.some((p) => !p.acknowledged) && (
          <p className="upload-drop-hint">확인한 뒤 검증을 다시 실행하면 상태가 갱신됩니다.</p>
        )}
      </section>

      {run.entities.length > 0 && (
        <EntityNotes entities={run.entities} onRegistered={onGlossaryChanged} canRegister={canManageGlossary} />
      )}

      {run.notes.length > 0 && (
        <details className="validation-notes">
          <summary>참고 {run.notes.length}건 (상태에 영향 없음)</summary>
          <ul>
            {run.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

const ENTITY_STATUS: Record<EntityNote['status'], string> = {
  unknown: '사전에 없음',
  ambiguous: '여러 대상',
  abolished: '폐지·개편',
}

/** 용어사전 확인 대상 이름. 차단·경고와 별도이며 검증 상태에 영향을 주지 않는다. */
function EntityNotes({
  entities,
  onRegistered,
  canRegister,
}: {
  entities: EntityNote[]
  onRegistered: () => void
  canRegister: boolean
}) {
  const [choice, setChoice] = useState<Record<string, string>>({})
  const [done, setDone] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  async function register(e: EntityNote) {
    const code = choice[e.value] ?? e.candidates[0]?.code
    if (!code) return
    setError(null)
    try {
      const created = await createAlias({ alias: e.value, target_code: code })
      setDone((prev) => ({ ...prev, [e.value]: created.target_name ?? code }))
      onRegistered()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="validation-group group-entity">
      <h3>용어사전 확인 대상 {entities.length}건 · 상태에 영향 없음</h3>
      <div className="upload-table-wrap">
        <table className="upload-table validation-issues">
          <thead>
            <tr>
              <th scope="col">이름</th>
              <th scope="col">상태</th>
              <th scope="col">{canRegister ? '연결할 대상 (별칭 등록)' : '후보'}</th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e, i) => (
              <tr key={`${e.field}:${e.value}:${i}`}>
                <th scope="row">
                  {e.value}
                  <div className="glossary-meta">
                    {e.count}행 · {e.files.join(', ')}
                  </div>
                </th>
                <td className="validation-message">
                  <span className="upload-tag">{ENTITY_STATUS[e.status]}</span> {e.note ?? ''}
                </td>
                <td>
                  {!canRegister ? (
                    <span className="upload-drop-hint">
                      {e.candidates.length
                        ? `${e.candidates.map((c) => `${c.name}(${c.code})`).join(', ')} — 사전 등록은 관리자에게 요청 (검토에서 이번 통합에만 연결할 수 있음)`
                        : '후보 없음 — 사전 등록은 관리자에게 요청'}
                    </span>
                  ) : e.registrable === false ? (
                    <span className="upload-drop-hint">개인정보 형식이 든 이름이라 별칭으로 등록할 수 없습니다 (원본 확인 필요)</span>
                  ) : done[e.value] ? (
                    <span className="mapping-message">→ {done[e.value]} 등록됨 (다시 검증하면 반영)</span>
                  ) : e.candidates.length ? (
                    <div className="mapping-pick">
                      <select
                        aria-label={`${e.value} 연결 대상`}
                        value={choice[e.value] ?? e.candidates[0].code}
                        onChange={(ev) => setChoice((prev) => ({ ...prev, [e.value]: ev.target.value }))}
                      >
                        {e.candidates.map((c) => (
                          <option key={c.code} value={c.code}>
                            {c.name} ({c.code})
                          </option>
                        ))}
                      </select>
                      <button type="button" className="mapping-confirm" onClick={() => void register(e)}>
                        별칭 등록
                      </button>
                    </div>
                  ) : (
                    <span className="upload-drop-hint">후보 없음 — 용어사전에서 검색해 등록</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

export default Validation
