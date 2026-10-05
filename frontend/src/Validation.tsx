import { useEffect, useState } from 'react'
import PiiNotice from './PiiNotice'
import {
  createAlias,
  listFiles,
  runValidation,
  type EntityNote,
  type StoredFile,
  type ValidationIssue,
  type ValidationRun,
  type ValidationStatus,
} from './api'

const STATUS_TEXT: Record<ValidationStatus, string> = {
  blocked: '검토 필요 — 차단 항목이 있어 통합할 수 없습니다',
  pii_confirmation_required: '개인정보 확인 필요 — 확인 후 통합할 수 있습니다',
  passed_with_warnings: '통합 가능 — 경고 항목을 확인하세요',
  passed: '통합 가능 — 문제가 없습니다',
}

const ENTITY_OPTIONS = [
  { value: '', label: '자동 (기관명 → 시군구 → 행정구역 → 시도)' },
  { value: 'org_name', label: '기관명' },
  { value: 'sido', label: '시도' },
  { value: 'sigungu', label: '시군구' },
  { value: 'region', label: '행정구역' },
  { value: 'region_code', label: '행정구역코드' },
]

function Validation({ refreshKey, onGlossaryChanged }: { refreshKey: number; onGlossaryChanged: () => void }) {
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

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      setRun(
        await runValidation({
          file_ids: [...targets],
          baseline_file_ids: [...baseline],
          expected_period: period.trim() || null,
          required_entities: entities
            .split(/[\n,]/)
            .map((s) => s.trim())
            .filter(Boolean),
          entity_field: entityField || null,
          change_threshold: threshold / 100,
        }),
      )
    } catch (err) {
      setError(`검증 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="validation">
      <h2>통합 전 검증</h2>
      {files.length === 0 ? (
        <p className="upload-drop-hint">먼저 파일을 업로드하세요.</p>
      ) : (
        <div className="upload-table-wrap">
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
              {files.map((f) => (
                <tr key={f.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${f.original_name} 검증 대상`}
                      checked={targets.has(f.id)}
                      onChange={(e) => {
                        setTargets(toggle(targets, f.id, e.target.checked))
                        if (e.target.checked) setBaseline(toggle(baseline, f.id, false))
                      }}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${f.original_name} 전월 자료`}
                      checked={baseline.has(f.id)}
                      onChange={(e) => {
                        setBaseline(toggle(baseline, f.id, e.target.checked))
                        if (e.target.checked) setTargets(toggle(targets, f.id, false))
                      }}
                    />
                  </td>
                  <td>
                    #{f.id} {f.original_name}
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
      <button type="button" className="mapping-run" disabled={busy || targets.size === 0} onClick={() => void submit()}>
        {busy ? '검증 중…' : `검증 실행 (${targets.size}개 파일)`}
      </button>
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

type ResultProps = { run: ValidationRun; onPiiChanged: () => void; onGlossaryChanged: () => void }

function ValidationResult({ run, onPiiChanged, onGlossaryChanged }: ResultProps) {
  return (
    <div className="validation-result">
      <p className={`validation-status status-${run.status}`} role="status">
        검증 #{run.id}: {STATUS_TEXT[run.status]}
      </p>

      <section className="validation-group group-block">
        <h3>차단 {run.blocks.length}건 · 통합 불가</h3>
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

      {run.entities.length > 0 && <EntityNotes entities={run.entities} onRegistered={onGlossaryChanged} />}

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
function EntityNotes({ entities, onRegistered }: { entities: EntityNote[]; onRegistered: () => void }) {
  const [choice, setChoice] = useState<Record<string, string>>({})
  const [done, setDone] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  async function register(e: EntityNote) {
    const picked = choice[e.value] ?? (e.candidates[0] ? `${e.candidates[0].kind}:${e.candidates[0].code}` : '')
    const [kind, code] = picked.split(':')
    if (!kind || !code) return
    setError(null)
    try {
      const created = await createAlias({ alias: e.value, kind: kind as 'org' | 'region', target_code: code })
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
              <th scope="col">연결할 대상 (별칭 등록)</th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e) => (
              <tr key={`${e.field}:${e.value}`}>
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
                  {done[e.value] ? (
                    <span className="mapping-message">→ {done[e.value]} 등록됨 (다시 검증하면 반영)</span>
                  ) : e.candidates.length ? (
                    <div className="mapping-pick">
                      <select
                        aria-label={`${e.value} 연결 대상`}
                        value={choice[e.value] ?? `${e.candidates[0].kind}:${e.candidates[0].code}`}
                        onChange={(ev) => setChoice((prev) => ({ ...prev, [e.value]: ev.target.value }))}
                      >
                        {e.candidates.map((c) => (
                          <option key={`${c.kind}:${c.code}`} value={`${c.kind}:${c.code}`}>
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
