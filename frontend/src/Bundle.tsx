import { useEffect, useState } from 'react'
import {
  AnalysisProblemError,
  planBundle,
  runBundle,
  runDownloadUrl,
  type AggKey,
  type BundleLink,
  type BundlePlan,
  type BundlePlanItem,
  type BundlePlanLink,
  type BundleRequest,
  type IntegrationRun,
} from './api'
import { Toggle } from './components/tailgrids/core/toggle'
import PiiNotice from './PiiNotice'
import { navigate, replaceSearch, useSearch } from './router'
import { Banner, NavLink, WorkflowSteps, type Step } from './Workflow'

const AGGS: { key: AggKey; label: string }[] = [
  { key: 'sum', label: '합계' },
  { key: 'mean', label: '평균' },
  { key: 'min', label: '최솟값' },
  { key: 'max', label: '최댓값' },
  { key: 'count', label: '유효 숫자 개수' },
]
const STORE = 'moajwo.bundle.draft' // 새로고침·뒤로 가기 뒤에도 같은 파일이면 설정을 이어 쓴다 (이 브라우저에만)
const SETTLE_MS = 250 // 입력하는 동안은 기다렸다가 마지막 구성만 확인한다

type LinkDraft = Omit<BundleLink, 'file_id' | 'sheet_id'>
type ItemDraft = { field_key: string; aggregates: AggKey[]; links: Record<string, LinkDraft> }
// picks: 결과 시트 순서 ('파일:시트'). null이면 아직 정하지 않음 → 서버가 정한 기본 순서로 시작
type Draft = { picks: string[] | null; summary: boolean; items: ItemDraft[] }
type Failure = { message: string; problems: string[] }

const EMPTY: Draft = { picks: null, summary: false, items: [] }
const keyOf = (r: { file_id: number; sheet_id: string }) => `${r.file_id}:${r.sheet_id}`
const refOf = (k: string) => ({ file_id: Number(k.split(':')[0]), sheet_id: k.split(':')[1] })

function filesFrom(search: string): number[] {
  const raw = new URLSearchParams(search).get('files') ?? ''
  return [...new Set(raw.split(',').map((v) => Number(v.trim())))].filter((n) => Number.isInteger(n) && n > 0)
}

function loadDraft(filesKey: string): Draft {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) ?? 'null') as { files: string; draft: Draft } | null
    return saved && saved.files === filesKey ? saved.draft : EMPTY
  } catch {
    return EMPTY
  }
}

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

function buildRequest(files: number[], draft: Draft): BundleRequest {
  const items = draft.summary
    ? draft.items.map((i) => ({
        field_key: i.field_key,
        aggregates: i.aggregates,
        links: Object.entries(i.links)
          .filter(([, l]) => l.exclude || l.unit || l.period || l.column_id || l.rows_are_records)
          .map(([k, l]) => ({ ...refOf(k), ...l })),
      }))
    : []
  return {
    file_ids: files,
    sheets: (draft.picks ?? []).filter((k) => files.includes(refOf(k).file_id)).map(refOf),
    summary: items.length ? { items } : null,
  }
}

/** 파일별 시트로 묶기: 고른 파일 → (취합 방식: 파일별 시트) → 시트·통계 요약 설정 → 결과 구성 확인 → 엑셀 만들기.
 * 구성이 바뀌면 다시 확인하고, 늦게 온 확인 응답은 버린다. 만들기는 지금 구성으로 확인한 결과(basis)가 있을 때만. */
function Bundle() {
  const search = useSearch()
  const files = filesFrom(search)
  const filesKey = files.join(',')
  const [draft, setDraft] = useState<Draft>(() => loadDraft(filesKey))
  const [draftFor, setDraftFor] = useState(filesKey)
  if (draftFor !== filesKey) {
    setDraftFor(filesKey)
    setDraft(loadDraft(filesKey))
  }
  const [checked, setChecked] = useState<{ key: string; plan: BundlePlan } | null>(null)
  const [checkError, setCheckError] = useState<{ key: string; message: string } | null>(null)
  const [version, setVersion] = useState(0) // 만들기 거절(바뀜) 뒤 같은 구성으로 다시 확인
  const [running, setRunning] = useState(false)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [made, setMade] = useState<{ key: string; run: IntegrationRun } | null>(null)
  const [newField, setNewField] = useState('')

  const request = buildRequest(files, draft)
  const reqKey = JSON.stringify(request)
  const plan = checked?.key === reqKey ? checked.plan : null
  const checking = files.length > 0 && !plan && checkError?.key !== reqKey

  useEffect(() => {
    try {
      sessionStorage.setItem(STORE, JSON.stringify({ files: filesKey, draft }))
    } catch {
      // 저장소를 쓸 수 없으면(사생활 보호 창 등) 이번 화면에서만 유지한다
    }
  }, [filesKey, draft])

  useEffect(() => {
    if (!filesKey) return
    const controller = new AbortController()
    const body = JSON.parse(reqKey) as BundleRequest
    const timer = window.setTimeout(() => {
      planBundle(body, controller.signal)
        .then((p) => {
          if (controller.signal.aborted) return // 구성이 바뀐 뒤 도착한 응답은 버린다
          setChecked({ key: reqKey, plan: p })
          setCheckError(null)
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setCheckError({ key: reqKey, message: errorText(err) })
        })
    }, SETTLE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [filesKey, reqKey, version])

  // 시트가 하나뿐인 파일은 자동으로 넣는다 (여러 시트인 파일은 직접 고름). 처음에는 서버가 정한 기본 순서로 시작
  useEffect(() => {
    if (!plan) return
    setDraft((d) => {
      const base = (d.picks ?? plan.sheets.map(keyOf)).filter((k) => files.includes(refOf(k).file_id))
      const add = plan.files
        .filter((f) => f.sheets.length === 1 && !base.includes(`${f.file_id}:s0`))
        .map((f) => `${f.file_id}:s0`)
      const next = [...base, ...add]
      return d.picks && next.join('|') === d.picks.join('|') ? d : { ...d, picks: next }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan])

  function update(fn: (d: Draft) => Draft) {
    setFailure(null)
    setDraft(fn)
  }

  function togglePick(k: string, on: boolean) {
    update((d) => {
      const picks = d.picks ?? []
      return { ...d, picks: on ? [...picks.filter((x) => x !== k), k] : picks.filter((x) => x !== k) }
    })
  }

  function move(k: string, by: number) {
    update((d) => {
      const picks = [...(d.picks ?? [])]
      const i = picks.indexOf(k)
      const j = i + by
      if (i < 0 || j < 0 || j >= picks.length) return d
      ;[picks[i], picks[j]] = [picks[j], picks[i]]
      return { ...d, picks }
    })
  }

  function removeFile(fid: number) {
    const rest = files.filter((f) => f !== fid)
    const q = new URLSearchParams(window.location.search)
    if (rest.length) q.set('files', rest.join(','))
    else q.delete('files')
    // 남은 파일의 설정은 이어 쓴다
    const nextDraft = { ...draft, picks: draft.picks?.filter((k) => refOf(k).file_id !== fid) ?? null }
    try {
      sessionStorage.setItem(STORE, JSON.stringify({ files: rest.join(','), draft: nextDraft }))
    } catch {
      // 저장소를 쓸 수 없으면 기본 설정으로 다시 시작한다
    }
    replaceSearch(q.toString() ? `?${q.toString()}` : '')
  }

  function setItem(index: number, fn: (i: ItemDraft) => ItemDraft) {
    update((d) => ({ ...d, items: d.items.map((it, n) => (n === index ? fn(it) : it)) }))
  }

  function setLink(index: number, k: string, patch: Partial<LinkDraft>) {
    setItem(index, (it) => ({ ...it, links: { ...it.links, [k]: { ...it.links[k], ...patch } } }))
  }

  async function make() {
    if (!plan || !plan.ready || running) return
    setRunning(true)
    setFailure(null)
    try {
      setMade({ key: reqKey, run: await runBundle(request, plan.basis) })
    } catch (err) {
      setFailure(
        err instanceof AnalysisProblemError
          ? { message: err.message, problems: err.problems }
          : { message: errorText(err), problems: [] },
      )
      setVersion((v) => v + 1) // 지금 상태로 다시 확인
    } finally {
      setRunning(false)
    }
  }

  const summaryEmpty = draft.summary && draft.items.length === 0
  const blockers = plan ? plan.problems : []
  let why: string | null = null
  if (!files.length) why = '고른 파일이 없습니다 — 홈에서 파일을 고르세요.'
  else if (checkError?.key === reqKey) why = '결과 구성을 확인하지 못했습니다 — 아래 오류를 확인하세요.'
  else if (!plan) why = '지금 구성을 확인하는 중입니다.'
  else if (summaryEmpty) why = "통계 요약 항목을 하나 이상 추가하거나 '통계 요약 시트 추가'를 끄세요."
  else if (blockers.length) why = `만들 수 없는 이유 ${blockers.length}건을 먼저 해결하세요 (위 목록).`
  else if (!plan.ready) why = '결과에 넣을 시트가 없습니다.'

  const steps: Step[] = [
    { key: 'files', state: files.length ? 'done' : 'todo', detail: `${files.length}개`, href: `/?files=${filesKey}` },
    { key: 'mode', state: 'done', detail: '파일별 시트', href: '/' },
    {
      key: 'settings',
      state: !plan ? 'todo' : plan.problems.length || summaryEmpty ? 'attention' : 'done',
      detail: draft.summary ? '시트·통계 요약' : '시트 (통계 요약 없음)',
      current: true,
    },
    { key: 'compose', state: plan ? (why ? 'attention' : 'done') : 'todo', detail: plan ? `시트 ${plan.sheet_count}개` : undefined },
    { key: 'make', state: made?.key === reqKey ? 'done' : 'todo', detail: made?.key === reqKey ? `실행 #${made.run.id}` : undefined },
  ]
  const multi = plan ? plan.files.filter((f) => f.sheets.length > 1) : []
  const usedFields = new Set(draft.items.map((i) => i.field_key))
  const addable = plan ? plan.fields.filter((f) => !usedFields.has(f.key)) : []
  const pickedField = addable.some((f) => f.key === newField) ? newField : (addable[0]?.key ?? '')

  return (
    <section className="bundle" aria-label="파일별 시트로 묶기">
      <WorkflowSteps steps={steps} label="파일별 시트로 묶기 단계" />
      <Banner tone="info" icon="info" title="앱이 읽은 값·열만 옮깁니다 — 엑셀 모양 그대로 복사가 아닙니다">
        <p>
          서식·병합·차트·그림·수식은 옮기지 않고, 시트마다 앞에 &lsquo;원본 행&rsquo;·&lsquo;행 종류&rsquo; 열을 붙입니다. 원본
          파일·열 매핑·학습은 바꾸지 않습니다. 검토·통계 검증을 거치지 않은 묶음입니다.
        </p>
      </Banner>

      {!files.length && (
        <Banner tone="warn" icon="alert" title="고른 파일이 없습니다">
          <p>
            홈의 파일 목록에서 파일을 고르고 결과 형태로 &lsquo;파일별 시트로 묶기&rsquo;를 고르세요.{' '}
            <NavLink href="/">홈으로</NavLink>
          </p>
        </Banner>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>1. 고른 파일 ({files.length}개)</legend>
          <ul className="bd-files">
            {(plan?.files ?? []).map((f) => (
              <li key={f.file_id} className={f.problems.length ? 'has-problem' : undefined}>
                <span className="bd-file-name">
                  #{f.file_id} {f.file_name}
                </span>
                <span className="mw-muted">
                  시트 {f.sheets.length}개{f.skipped_sheets.length > 0 && ` · 설명 시트 ${f.skipped_sheets.join(', ')}는 앱이 읽지 않아 넣지 않음`}
                </span>
                <button type="button" className="login-link" onClick={() => removeFile(f.file_id)}>
                  이 파일 빼기
                </button>
                {f.pii === 'needs_ack' && f.pii_summary && (
                  <PiiNotice fileId={f.file_id} pii={f.pii_summary} onAcknowledged={() => setVersion((v) => v + 1)} />
                )}
              </li>
            ))}
          </ul>
          <p className="mw-muted">
            파일을 더 고르려면 <NavLink href={`/?files=${filesKey}`}>홈의 파일 목록</NavLink>으로 돌아가세요 (최대 {plan?.limits.files ?? 50}개).
          </p>
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>2. 필요한 설정</legend>
          <h3 className="bd-sub">넣을 시트</h3>
          {multi.length === 0 && <p className="mw-muted">고른 파일이 모두 시트 하나라 그대로 넣습니다 (파일마다 결과 시트 하나).</p>}
          {multi.map((f) => {
            const none = !f.sheets.some((s) => draft.picks?.includes(`${f.file_id}:${s.sheet_id}`))
            return (
              <div key={f.file_id} className={`bd-multi${none ? ' needs-pick' : ''}`}>
                <p className="bd-multi-title">
                  #{f.file_id} {f.file_name} — 시트 {f.sheets.length}개 중 넣을 시트를 고르세요 (고른 시트마다 결과 시트 하나)
                </p>
                <div className="bd-multi-list" role="group" aria-label={`#${f.file_id} 넣을 시트`}>
                  {f.sheets.map((s) => {
                    const k = `${f.file_id}:${s.sheet_id}`
                    return (
                      <label key={k}>
                        <input type="checkbox" checked={draft.picks?.includes(k) ?? false} onChange={(e) => togglePick(k, e.target.checked)} />
                        {s.sheet} <span className="mw-muted">({s.rows.toLocaleString('ko-KR')}행)</span>
                      </label>
                    )
                  })}
                </div>
                {none && <p className="wk-why" role="note"><strong>고르지 않음</strong> 시트를 하나 이상 고르거나 이 파일을 빼세요.</p>}
              </div>
            )
          })}

          <h3 className="bd-sub">통계 요약 시트 (선택)</h3>
          <Toggle
            size="md"
            label="통계 요약 시트 추가"
            checked={draft.summary}
            onChange={(e) => update((d) => ({ ...d, summary: e.target.checked }))}
          />
          {!draft.summary && (
            <p className="mw-muted">끄면 요약 시트를 만들지 않고 계산도 하지 않습니다 (데이터 시트만).</p>
          )}
          {draft.summary && (
            <div className="bd-summary">
              <p className="mw-muted">
                사용자가 열 매핑에서 확정한 표준 항목으로 연결합니다 (열 위치·이름이 아니라 확정한 뜻으로, &lsquo;자동 제안&rsquo;은 쓰지
                않음). 계산은 데이터 분석과 같은 규칙입니다 — 평균은 유효한 숫자의 합 ÷ 개수, 빈칸은 0이 아니라 뺍니다. 요약은 만든
                때의 값이며 자동으로 다시 계산되지 않습니다. 한 항목에 시트 {plan?.limits.summary_sheets ?? 20}개까지 계산합니다
                (데이터 분석과 같은 한도).
              </p>
              {draft.items.map((it, index) => {
                const out = plan?.summary?.items[index]
                return (
                  <SummaryItem
                    key={it.field_key}
                    draft={it}
                    out={out && out.field_key === it.field_key ? out : null}
                    onAggregates={(aggregates) => setItem(index, (x) => ({ ...x, aggregates }))}
                    onLink={(k, patch) => setLink(index, k, patch)}
                    onRemove={() => update((d) => ({ ...d, items: d.items.filter((_, n) => n !== index) }))}
                  />
                )
              })}
              {plan && plan.fields.length === 0 && (
                <p className="wk-why" role="note">
                  <strong>추가할 항목 없음</strong> 고른 시트에 확정한 수량 항목이 없습니다 — 열 매핑에서 확정하면 요약에 쓸 수 있습니다
                  (요약 없이 묶기는 그대로 됩니다).
                </p>
              )}
              {addable.length > 0 && draft.items.length < (plan?.limits.items ?? 10) && (
                <div className="bd-add">
                  <label>
                    요약할 항목
                    <select value={pickedField} onChange={(e) => setNewField(e.target.value)}>
                      {addable.map((f) => (
                        <option key={f.key} value={f.key}>
                          {f.label} (확정한 시트 {f.sheets}개)
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="mapping-confirm"
                    onClick={() =>
                      update((d) => ({ ...d, items: [...d.items, { field_key: pickedField, aggregates: ['sum'], links: {} }] }))
                    }
                  >
                    항목 추가
                  </button>
                </div>
              )}
            </div>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>3. 결과 구성 확인</legend>
          {checkError?.key === reqKey && (
            <p className="upload-error" role="alert">
              결과 구성을 확인하지 못했습니다: {checkError.message}
            </p>
          )}
          {!plan && checking && <p className="mw-muted" role="status">지금 구성을 확인하는 중…</p>}
          {plan && (
            <>
              <p className="bd-total" role="status">
                결과 시트 <strong>{plan.sheet_count}</strong>개 · 넣을 행 <strong>{plan.total_rows.toLocaleString('ko-KR')}</strong>행 (한 번에{' '}
                {plan.limits.rows.toLocaleString('ko-KR')}행 · 시트 {plan.limits.sheets}개까지 — 넘으면 일부만 넣지 않고 만들지 않음)
              </p>
              {plan.problems.length > 0 && (
                <div className="upload-error bd-problems" role="alert">
                  <p>
                    <strong>만들 수 없는 이유 {plan.problems.length}건</strong>
                  </p>
                  <ul>
                    {plan.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}
              {plan.warnings.length > 0 && (
                <ul className="mapping-warn bd-warnings">
                  {plan.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              )}
              <ol className="bd-sheets" aria-label="결과 시트 순서">
                {plan.sheets.map((s, i) => (
                  <li key={keyOf(s)}>
                    <span className="bd-sheet-no">{i + 1}</span>
                    <div className="bd-sheet-body">
                      <p className="bd-sheet-name">
                        <strong>{s.name}</strong>
                        {s.renamed.length > 0 && <span className="upload-tag mapping-review">이름 바뀜</span>}
                      </p>
                      <p className="mw-muted">
                        원본 #{s.file_id} {s.file_name} / {s.sheet} · {s.rows.toLocaleString('ko-KR')}행 (데이터 {s.kinds.data}
                        {s.kinds.subtotal > 0 && ` · 소계·합계 ${s.kinds.subtotal}`}
                        {s.kinds.note > 0 && ` · 주석 ${s.kinds.note}`}) · 열 {s.columns}개
                      </p>
                      <SheetNotes s={s} />
                    </div>
                    <div className="bd-move">
                      <button type="button" className="mapping-confirm" aria-label={`${s.name} 위로`} disabled={i === 0} onClick={() => move(keyOf(s), -1)}>
                        위로
                      </button>
                      <button
                        type="button"
                        className="mapping-confirm"
                        aria-label={`${s.name} 아래로`}
                        disabled={i === plan.sheets.length - 1}
                        onClick={() => move(keyOf(s), 1)}
                      >
                        아래로
                      </button>
                    </div>
                  </li>
                ))}
                {plan.summary && (
                  <li className="is-summary">
                    <span className="bd-sheet-no">{plan.sheets.length + 1}</span>
                    <div className="bd-sheet-body">
                      <p className="bd-sheet-name">
                        <strong>{plan.summary.sheet_name}</strong> <span className="mw-muted">(항상 마지막)</span>
                      </p>
                      <p className="mw-muted">
                        항목 {plan.summary.items.map((i) => `${i.field_label}(${i.aggregate_labels.join('·')}, ${i.coverage.text})`).join(' · ')} — 만든
                        때의 값, 자동으로 다시 계산되지 않음
                      </p>
                    </div>
                  </li>
                )}
              </ol>
            </>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>4. 엑셀 만들기</legend>
          <div className="admin-actions">
            <button type="button" className="mapping-run" disabled={why !== null || running} onClick={() => void make()}>
              {running ? '만드는 중…' : `엑셀 만들기 (시트 ${plan?.sheet_count ?? '-'}개)`}
            </button>
          </div>
          {why && !running && (
            <p className="wk-why" role="note">
              <strong>누를 수 없음</strong> {why}
            </p>
          )}
          {failure && (
            <div className="upload-error" role="alert">
              <p>{failure.message}</p>
              {failure.problems.length > 0 && (
                <ul>
                  {failure.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {made && (
            <div className="bd-made" role="status">
              <p>
                <strong>실행 #{made.run.id} 완료</strong> — {made.run.file_name} ·{' '}
                <a href={runDownloadUrl(made.run.id)}>내려받기</a> ·{' '}
                <a
                  href="/runs"
                  onClick={(e) => {
                    e.preventDefault()
                    navigate('/runs')
                  }}
                >
                  실행 기록에서 보기
                </a>
              </p>
              <p className="mw-muted">시트 순서: {(made.run.summary.sheet_order ?? []).join(' → ')}</p>
              {made.key !== reqKey && <p className="mapping-warn">구성을 바꿨습니다 — 위 결과는 바꾸기 전 구성으로 만든 파일입니다.</p>}
            </div>
          )}
        </fieldset>
      )}
    </section>
  )
}

function SheetNotes({ s }: { s: BundlePlan['sheets'][number] }) {
  const notes: string[] = [...s.renamed.map((r) => `이름: ${r}`)]
  if (s.title_rows > 0) notes.push(`머리글 위 ${s.title_rows}행(제목·단위 등)은 넣지 않음`)
  if (s.empty_rows > 0) notes.push(`빈 행 ${s.empty_rows}개는 넣지 않음`)
  if (s.two_level_header) notes.push('2단 머리글은 한 줄 열 이름으로 합쳐 넣음')
  if (s.formula_missing > 0) notes.push(`수식 결과값이 저장돼 있지 않은 칸 ${s.formula_missing}개는 비워 두고 표시 (0으로 채우지 않음)`)
  if (s.formula_cached > 0) notes.push(`수식 칸 ${s.formula_cached}개는 저장된 결과값으로 넣음 (수식은 옮기지 않음)`)
  if (!s.formula_checked && s.file_name.toLowerCase().endsWith('.xls')) notes.push('xls 파일은 수식 여부를 확인할 수 없어 저장된 값을 넣음')
  if (s.long_numbers > 0) notes.push(`15자리를 넘는 숫자 ${s.long_numbers}개는 엑셀이 끝자리를 정확히 담지 못함`)
  if (!notes.length) return null
  return (
    <ul className="bd-notes">
      {notes.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  )
}

const LINK_STATE: Record<BundlePlanLink['status'], { text: string; cls: string }> = {
  ok: { text: '포함', cls: 'mapping-confirmed' },
  needs_input: { text: '확인 필요', cls: 'mapping-review' },
  excluded: { text: "자동 제외 — '빼기' 확정 필요", cls: 'mapping-unresolved' },
  user_excluded: { text: '뺌 (사용자)', cls: '' },
  unchecked: { text: '확인 전', cls: '' },
}

function SummaryItem({
  draft,
  out,
  onAggregates,
  onLink,
  onRemove,
}: {
  draft: ItemDraft
  out: BundlePlanItem | null
  onAggregates: (a: AggKey[]) => void
  onLink: (k: string, patch: Partial<LinkDraft>) => void
  onRemove: () => void
}) {
  return (
    <div className={`bd-item${out && !out.ready ? ' needs-fix' : ''}`}>
      <div className="bd-item-head">
        <strong>{out?.field_label ?? draft.field_key}</strong>
        {out && <span className={`upload-tag ${out.ready ? 'mapping-confirmed' : 'mapping-review'}`}>{out.coverage.text}</span>}
        {out?.unit && <span className="mw-muted">단위 {out.unit}</span>}
        {out && out.periods.length > 0 && <span className="mw-muted">기간 {out.periods.join(', ')}</span>}
        <button type="button" className="mapping-confirm wk-danger" onClick={onRemove}>
          항목 빼기
        </button>
      </div>
      <div className="bd-aggs" role="group" aria-label={`${out?.field_label ?? draft.field_key} 계산 방식`}>
        {AGGS.map((a) => {
          const on = draft.aggregates.includes(a.key)
          const last = on && draft.aggregates.length === 1
          return (
            <label key={a.key} title={last ? '계산 방식은 하나 이상 골라야 합니다' : undefined}>
              <input
                type="checkbox"
                checked={on}
                disabled={last}
                onChange={(e) =>
                  onAggregates(AGGS.map((x) => x.key).filter((k) => (k === a.key ? e.target.checked : draft.aggregates.includes(k))))
                }
              />
              {a.label}
            </label>
          )
        })}
      </div>
      {out && out.problems.length > 0 && (
        <ul className="upload-error bd-item-problems">
          {out.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {out && (
        <div className="upload-table-wrap">
          <table className="upload-table bd-links">
            <thead>
              <tr>
                <th scope="col">결과 시트</th>
                <th scope="col">원본 파일 / 시트</th>
                <th scope="col">열</th>
                <th scope="col">표준 항목 (사용자 확정)</th>
                <th scope="col">단위</th>
                <th scope="col">기간·기준일</th>
                <th scope="col">포함</th>
                <th scope="col">상태·사유</th>
              </tr>
            </thead>
            <tbody>
              {out.links.map((lk) => {
                const k = keyOf(lk)
                const d = draft.links[k] ?? {}
                const needUnit = lk.reasons.some((r) => r.startsWith('단위를 확인할 수 없음'))
                const needPeriod = lk.reasons.some((r) => r.startsWith('기간을 확인할 수 없음'))
                const pickColumn = (lk.candidates?.length ?? 0) > 1 && lk.reasons.some((r) => r.includes('분석할 열을 고르세요'))
                return (
                  <tr key={k} className={`is-${lk.status}`}>
                    <th scope="row">{lk.output_sheet}</th>
                    <td>
                      #{lk.file_id} {lk.file_name} / {lk.sheet}
                    </td>
                    <td>
                      {pickColumn ? (
                        <select aria-label={`${lk.output_sheet} 열`} value={d.column_id ?? ''} onChange={(e) => onLink(k, { column_id: e.target.value || null })}>
                          <option value="">열 고르기</option>
                          {lk.candidates?.map((c) => (
                            <option key={c.column_id} value={c.column_id}>
                              {c.column}
                            </option>
                          ))}
                        </select>
                      ) : (
                        (lk.column ?? '-')
                      )}
                    </td>
                    <td>{lk.status === 'user_excluded' ? '-' : (lk.field_label ?? '-')}</td>
                    <td>
                      {needUnit || d.unit ? (
                        <input
                          type="text"
                          className="bd-input"
                          aria-label={`${lk.output_sheet} 단위`}
                          placeholder="예: 명"
                          value={d.unit ?? ''}
                          maxLength={20}
                          onChange={(e) => onLink(k, { unit: e.target.value.trim() || null })}
                        />
                      ) : lk.unit ? (
                        `${lk.unit} (${lk.unit_source ?? '-'})`
                      ) : (
                        '-'
                      )}
                    </td>
                    <td>
                      {needPeriod || d.period ? (
                        <input
                          type="text"
                          className="bd-input"
                          aria-label={`${lk.output_sheet} 기간`}
                          placeholder="2026-09"
                          value={d.period ?? ''}
                          onChange={(e) => onLink(k, { period: /^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$/.test(e.target.value.trim()) ? e.target.value.trim() : null })}
                        />
                      ) : lk.period ? (
                        `${lk.period} (${lk.period_source ?? '-'})`
                      ) : lk.period_columns?.length ? (
                        `행의 ${lk.period_columns.join('·')} 열`
                      ) : (
                        '-'
                      )}
                    </td>
                    <td>
                      <label className="bd-include">
                        <input
                          type="checkbox"
                          checked={!d.exclude}
                          onChange={(e) => onLink(k, { exclude: !e.target.checked, exclude_reason: e.target.checked ? null : (d.exclude_reason ?? null) })}
                        />
                        포함
                      </label>
                      {d.exclude && (
                        <input
                          type="text"
                          className="bd-input"
                          aria-label={`${lk.output_sheet} 뺀 이유`}
                          placeholder="뺀 이유 (선택)"
                          maxLength={100}
                          value={d.exclude_reason ?? ''}
                          onChange={(e) => onLink(k, { exclude_reason: e.target.value || null })}
                        />
                      )}
                    </td>
                    <td>
                      <span className={`upload-tag ${LINK_STATE[lk.status].cls}`}>{LINK_STATE[lk.status].text}</span>
                      {(lk.exclude_reason || lk.reasons.length > 0) && (
                        <span className="bd-reason">{lk.exclude_reason ?? lk.reasons.join('; ')}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {out?.rows && (
        <p className="mw-muted">
          계산에 들어갈 행 {out.rows.candidate.toLocaleString('ko-KR')}개
          {Object.keys(out.rows.excluded).length > 0 &&
            ` · 빼는 행: ${Object.entries(out.rows.excluded)
              .map(([r, n]) => `${out.reason_labels[r] ?? r} ${n}개`)
              .join(', ')}`}
        </p>
      )}
    </div>
  )
}

export default Bundle
