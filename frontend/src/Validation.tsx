import { useEffect, useState } from 'react'
import { RadioInput } from './components/tailgrids/core/radio-input'
import { navigate, replaceSearch, useSearch } from './router'
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

// 결과 형태: 하나의 통합표(검토 → 통합 결과) · 파일별 시트로 묶기(시트 하나씩 + 통계 요약 선택) · 공통 key로 연결하기(사용자별 한 행)
export type ResultMode = 'table' | 'sheets' | 'keyjoin'
const MODE_KEY = 'moajwo.home.mode' // 이 브라우저에서 마지막으로 고른 결과 형태
const KEYJOIN_MIN_FILES = 2 // 서버의 최소 입력 수 (keyjoin MAX/MIN_INPUTS) — 한 화면은 파일마다 시트 하나를 입력으로 쓴다
const MODES: { value: ResultMode; title: string; desc: string; example: string }[] = [
  {
    value: 'table',
    title: '하나의 통합표로 만들기',
    desc: '여러 파일의 같은 항목을 한 표로 합칩니다. 열 매핑을 확인하고 사전 검토를 거쳐 통합 결과를 만듭니다.',
    example: '예: a·b·c·d → 시트 「통합 데이터」 하나 (+ 변경 내역·처리 요약)',
  },
  {
    value: 'sheets',
    title: '파일별 시트로 묶기',
    desc: '고른 시트를 결과 파일의 시트 하나씩으로 옮깁니다. 검토·열 매핑 없이도 됩니다. 통계 요약 시트는 선택입니다.',
    example: '예: a·b·c·d → 시트 a·b·c·d (요약을 켜면 + 「통계 요약」)',
  },
  {
    value: 'keyjoin',
    title: '공통 key로 연결하기',
    desc: '여러 파일의 같은 사용자를 key(사번·ID 등)로 연결해 사용자별 한 행을 만듭니다. 열 매핑·검토 없이도 됩니다.',
    example: '예: A(사번·부서)+B(사용자번호·교육 건수)… → 시트 「연결 결과」 (+ 출처·연결 요약)',
  },
]

function savedMode(): ResultMode {
  try {
    const saved = localStorage.getItem(MODE_KEY)
    return saved === 'sheets' || saved === 'keyjoin' ? saved : 'table'
  } catch {
    return 'table'
  }
}

// 안내에 보일 파일 번호 (많으면 앞 몇 개와 개수)
function idList(ids: (number | string)[], shown = 5): string {
  const head = ids.slice(0, shown).map((id) => `#${id}`).join(', ')
  return ids.length > shown ? `${head} 외 ${ids.length - shown}개` : head
}

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
  onMode?: (mode: ResultMode) => void // 고른 결과 형태 (홈 단계 안내)
}

function Validation({
  refreshKey,
  canManageGlossary = true,
  onGlossaryChanged,
  onOpenReview,
  onSelection,
  onMode,
}: ValidationProps) {
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
  const [mode, setMode] = useState<ResultMode>(savedMode)
  const search = useSearch()
  const wanted = window.location.pathname === '/' ? new URLSearchParams(search).get('files') : null

  // 열 매핑 화면의 '다음: 이 파일로 검사·검토' (/?files=번호) — 그 파일을 검사 대상으로 골라 둔다 (실행은 하지 않음)
  // 요청을 받아들이지 못하면(한도 초과·하나도 찾지 못함) 이전 검증 대상도 비운다 — 안내와 실제 선택이 다르지 않게,
  // 이전 대상으로 검사·검토가 진행되지 않게. 한도를 넘으면 앞부분만 고르지도 않는다 (일부만 반영하지 않음)
  const [applied, setApplied] = useState<string | null>(null)
  const [preselectNote, setPreselectNote] = useState<string | null>(null)
  if (!wanted && applied !== null) setApplied(null)
  if (wanted && files.length > 0 && wanted !== applied) {
    setApplied(wanted)
    const asked = [...new Set(wanted.split(',').map((v) => v.trim()).filter(Boolean))]
    const found = asked.map(Number).filter((id) => Number.isInteger(id) && files.some((f) => f.id === id))
    const missing = asked.filter((v) => !found.includes(Number(v)))
    const cleared = targets.size > 0 ? ' 이전에 고른 검증 대상도 비웠습니다.' : ''
    if (found.length > MAX_TARGETS || found.length === 0) {
      setTargets(new Set())
      setPreselectNote(
        found.length
          ? `넘어온 파일 ${found.length}개는 한 번에 검사할 수 있는 ${MAX_TARGETS}개를 넘어 하나도 고르지 않았습니다.${cleared} 아래 목록에서 ${MAX_TARGETS}개까지 직접 고르세요.`
          : `넘어온 파일 ${asked.length}개(${idList(missing)})를 목록에서 찾지 못해 하나도 고르지 않았습니다.${cleared}`,
      )
    } else {
      setTargets(new Set(found))
      setBaseline((prev) => new Set([...prev].filter((id) => !found.includes(id))))
      setPreselectNote(
        missing.length
          ? `넘어온 파일 ${asked.length}개 중 ${found.length}개(${idList(found)})를 골랐습니다 — 목록에 없는 ${missing.length}개(${idList(missing)})는 뺐습니다.`
          : null,
      )
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
    onMode?.(mode)
    try {
      localStorage.setItem(MODE_KEY, mode)
    } catch {
      // 저장소를 쓸 수 없으면 이번 화면에서만 유지한다
    }
  }, [mode, onMode])

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
      <p className="wk-card-desc">검증 대상은 이번에 검사할 파일, 전월 자료는 비교에만 쓰는 지난 자료입니다.</p>
      {files.length === 0 ? (
        <Banner tone="info" icon="file" title="아직 올린 파일이 없습니다">
          <p>위의 '파일 업로드'에서 파일을 올리면 여기서 검사할 파일을 고를 수 있습니다.</p>
        </Banner>
      ) : (
        <div className="wk-val-grid">
          <div className="wk-val-main">
            <div className="wk-pickbar">
              <input
                type="search"
                className="mw-search"
                placeholder="파일 번호·이름 검색"
                aria-label="검사할 파일 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {/* 상태 영역에는 고른 개수만 둔다 (고를 때마다 전달). 전체 파일 수는 같은 줄에 보이기만 한다 —
                  업로드 뒤 목록이 갱신될 때 바뀌지 않은 고른 개수가 업로드 결과 안내 바로 뒤에 다시 전달되지 않게 */}
              <span className="wk-pickcount">
                <span role="status">
                  검증 대상 <strong>{targets.size}</strong>개 (최대 {MAX_TARGETS}) · 전월 자료 <strong>{baseline.size}</strong>개
                </span>{' '}
                · 전체 {files.length}개
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
            {preselectNote && (
              <p className="mapping-warn" role="status">
                {preselectNote}{' '}
                <button type="button" className="login-link" onClick={() => setPreselectNote(null)}>
                  안내 닫기
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
          </div>
          <aside className="wk-val-side" aria-label="검사 시작">
            <h3 className="wk-val-side-title">검사 시작</h3>
            <p className="wk-val-summary">
              검증 대상 <strong>{targets.size}</strong>개 · 전월 자료 <strong>{baseline.size}</strong>개
            </p>
            {targets.size === 0 && (
              <p className="wk-val-hint wk-val-empty">표에서 검증 대상을 1개 이상 고르면 시작할 수 있습니다.</p>
            )}
            {targets.size > 0 && (
              <ul className="wk-val-picked" aria-label="고른 검증 대상">
                {[...targets].slice(0, 4).map((id) => (
                  <li key={id} title={files.find((f) => f.id === id)?.display_name}>
                    #{id} {files.find((f) => f.id === id)?.display_name}
                  </li>
                ))}
                {targets.size > 4 && <li className="wk-val-more-count">외 {targets.size - 4}개</li>}
              </ul>
            )}
            <fieldset className="wk-mode">
              <legend>결과 형태</legend>
              {MODES.map((m) => (
                <label key={m.value} className={`wk-mode-card${mode === m.value ? ' is-picked' : ''}`}>
                  <RadioInput
                    name="wk-result-mode"
                    value={m.value}
                    size="md"
                    checked={mode === m.value}
                    onChange={() => setMode(m.value)}
                    className="wk-mode-radio"
                  />
                  <span className="wk-mode-text">
                    <strong>{m.title}</strong>
                    <span>{m.desc}</span>
                    <span className="wk-mode-example">{m.example}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            {mode === 'sheets' && (
              <div className="wk-mode-next">
                <button
                  type="button"
                  className="wk-btn-primary"
                  aria-describedby="wk-hint-bundle"
                  disabled={targets.size === 0}
                  onClick={() => navigate(`/bundle?files=${[...targets].join(',')}`, { focus: { to: 'page' } })}
                >
                  다음: 시트·통계 요약 설정 ({targets.size}개 파일)
                </button>
                <p className="wk-val-hint" id="wk-hint-bundle">
                  다음 화면에서 넣을 시트·순서·통계 요약(선택)을 정하고 결과 구성을 확인한 뒤 엑셀을 만듭니다.
                </p>
                {baseline.size > 0 && (
                  <p className="wk-why" role="note">
                    전월 자료 {baseline.size}개는 통합표 검사에만 씁니다 — 파일별 시트로 묶기에는 넣지 않습니다.
                  </p>
                )}
              </div>
            )}
            {mode === 'keyjoin' && (
              <div className="wk-mode-next">
                <button
                  type="button"
                  className="wk-btn-primary"
                  aria-describedby="wk-hint-keyjoin"
                  disabled={targets.size < KEYJOIN_MIN_FILES}
                  onClick={() => navigate(`/keyjoin?files=${[...targets].join(',')}`, { focus: { to: 'page' } })}
                >
                  다음: key·가져올 열 설정 ({targets.size}개 파일)
                </button>
                <p className="wk-val-hint" id="wk-hint-keyjoin">
                  다음 화면에서 입력마다 key 열(단일·복합)과 가져올 열·결과 이름·포함 범위를 정하고, 연결 결과·문제를 확인한 뒤
                  엑셀을 만듭니다. 파일 {KEYJOIN_MIN_FILES}개 이상이 필요합니다.
                </p>
                {targets.size > 0 && targets.size < KEYJOIN_MIN_FILES && (
                  <p className="wk-why" role="note">
                    <strong>누를 수 없음</strong> 연결하려면 검증 대상 파일을 {KEYJOIN_MIN_FILES}개 이상 고르세요.
                  </p>
                )}
                {baseline.size > 0 && (
                  <p className="wk-why" role="note">
                    전월 자료 {baseline.size}개는 통합표 검사에만 씁니다 — 공통 key 연결에는 넣지 않습니다.
                  </p>
                )}
              </div>
            )}
            {mode === 'table' && (
              <>
                {/* 안내 문장은 이름에 섞지 않고 설명(aria-describedby)으로 연결한다 */}
                <label className="wk-val-field">
                  기준 기간
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder="2026-09 또는 2026"
                    aria-describedby="wk-hint-period"
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                  />
                </label>
                <p className="wk-val-hint wk-val-hint-field" id="wk-hint-period">
                  입력하면 이 기간과 다른 자료를 찾아 알려 줍니다 (선택).
                </p>
                <button
                  type="button"
                  className="wk-btn-primary"
                  aria-describedby="wk-hint-review"
                  disabled={busy || targets.size === 0}
                  onClick={() => onOpenReview(buildRequest())}
                >
                  사전 검토 열기 (통합 전)
                </button>
                <p className="wk-val-hint" id="wk-hint-review">
                  문제를 확인·승인한 뒤 통합 결과까지 이어집니다.
                </p>
                <button
                  type="button"
                  className="wk-btn-outline"
                  aria-describedby="wk-hint-validate"
                  disabled={busy || targets.size === 0}
                  onClick={() => void submit()}
                >
                  {busy ? '검증 중…' : `검증 실행 (${targets.size}개 파일)`}
                </button>
                <p className="wk-val-hint" id="wk-hint-validate">
                  검사 결과만 빠르게 봅니다 (검토는 만들지 않음).
                </p>
                <details className="wk-val-more">
                  <summary>추가 검사 설정</summary>
                  <div className="validation-form">
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
                </details>
              </>
            )}
          </aside>
        </div>
      )}
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
