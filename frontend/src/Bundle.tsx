import { useEffect, useState } from 'react'
import {
  AnalysisProblemError,
  outcomeText,
  outcomeUnknown,
  planBundle,
  runBundle,
  type AggKey,
  type BundleItem,
  type BundleLink,
  type BundlePlan,
  type BundlePlanItem,
  type BundlePlanLink,
  type BundleRequest,
  type IntegrationRun,
} from './api'
import { SOURCE_NAMES_DIFFER, sourceText } from './analysisFields'
import { loadFields } from './mappingText'
import { Toggle } from './components/tailgrids/core/toggle'
import PiiNotice from './PiiNotice'
import { navigate, replaceSearch, useSearch } from './router'
import { RunDownloadLink } from './RunDownloadLink'
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
const PERIOD = /^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$/ // 서버(TargetIn.period)와 같은 형식

type Level = 'lower' | 'upper'
// 입력란은 사용자가 친 글자 그대로(…_input) 두고, 서버에는 보낼 수 있는 값만 보낸다 (buildRequest)
type LinkDraft = {
  column_id?: string | null
  unit_input?: string
  period_input?: string
  rows_are_records?: boolean
  exclude?: boolean // 이 항목 계산에서만 뺌 (시트는 결과 파일에 그대로 들어감)
  exclude_reason_input?: string
}
// variant·unit·region_level: 계산 엔진이 요구할 때만 화면에서 고른다 (undefined·null = 고르지 않음, variant ''는 '구분 없음')
type ItemDraft = {
  field_key: string
  aggregates: AggKey[]
  variant?: string | null
  unit?: string | null
  region_level?: Level | null
  links: Record<string, LinkDraft>
}
// picks: 결과 시트 순서 ('파일:시트'). null이면 아직 정하지 않음 → 서버가 정한 기본 순서로 시작
type Draft = { picks: string[] | null; summary: boolean; items: ItemDraft[] }
type Failure = { message: string; problems: string[]; unknown?: boolean } // unknown = 결과를 아직 모름 (실패 아님)
type Invalid = { key: string; field_key: string; text: string }

const EMPTY: Draft = { picks: null, summary: false, items: [] }
const keyOf = (r: { file_id: number; sheet_id: string }) => `${r.file_id}:${r.sheet_id}`
const refOf = (k: string) => ({ file_id: Number(k.split(':')[0]), sheet_id: k.split(':')[1] })

function filesFrom(search: string): number[] {
  const raw = new URLSearchParams(search).get('files') ?? ''
  return [...new Set(raw.split(',').map((v) => Number(v.trim())))].filter((n) => Number.isInteger(n) && n > 0)
}

/** 기간 입력: 비면 보내지 않음, 완성된 형식이면 그 값, 아니면 '잘못됨'(보내지 않고 생성도 막음 — 지우거나 이전 값으로 바꾸지 않음) */
function periodOf(text: string | undefined): { value: string | null; bad: boolean } {
  const t = (text ?? '').trim()
  if (!t) return { value: null, bad: false }
  return PERIOD.test(t) ? { value: t, bad: false } : { value: null, bad: true }
}

// 예전 형식(unit·period·exclude_reason을 바로 저장)으로 남은 설정도 이어 쓴다
function normalizeLink(raw: LinkDraft & { unit?: string | null; period?: string | null; exclude_reason?: string | null }): LinkDraft {
  const { unit, period, exclude_reason, ...rest } = raw
  return {
    ...rest,
    unit_input: rest.unit_input ?? unit ?? undefined,
    period_input: rest.period_input ?? period ?? undefined,
    exclude_reason_input: rest.exclude_reason_input ?? exclude_reason ?? undefined,
  }
}

function loadDraft(filesKey: string): Draft {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) ?? 'null') as { files: string; draft: Draft } | null
    if (!saved || saved.files !== filesKey) return EMPTY
    const d = saved.draft
    return {
      ...d,
      items: d.items.map((i) => ({
        ...i,
        links: Object.fromEntries(Object.entries(i.links ?? {}).map(([k, l]) => [k, normalizeLink(l)])),
      })),
    }
  } catch {
    return EMPTY
  }
}

function saveDraft(filesKey: string, draft: Draft) {
  try {
    sessionStorage.setItem(STORE, JSON.stringify({ files: filesKey, draft }))
  } catch {
    // 저장소를 쓸 수 없으면(사생활 보호 창 등) 이번 화면에서만 유지한다
  }
}

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

/** 지금 설정으로 보낼 요청과 고쳐야 할 입력. 결과에 넣지 않은 파일·시트의 연결은 보내지 않는다 (서버는 그런 연결을 거절함). */
function buildRequest(files: number[], draft: Draft): { request: BundleRequest; invalid: Invalid[] } {
  const placed = new Set((draft.picks ?? []).filter((k) => files.includes(refOf(k).file_id)))
  const invalid: Invalid[] = []
  const items: BundleItem[] = draft.summary
    ? draft.items.map((i) => {
        const links: BundleLink[] = []
        for (const [k, l] of Object.entries(i.links)) {
          if (!placed.has(k)) continue
          const period = periodOf(l.period_input)
          if (period.bad) invalid.push({ key: k, field_key: i.field_key, text: l.period_input ?? '' })
          const unit = (l.unit_input ?? '').trim()
          const reason = (l.exclude_reason_input ?? '').trim()
          const link: BundleLink = { ...refOf(k) }
          if (l.column_id) link.column_id = l.column_id
          if (unit) link.unit = unit
          if (period.value) link.period = period.value
          if (l.rows_are_records) link.rows_are_records = true
          if (l.exclude) {
            link.exclude = true
            if (reason) link.exclude_reason = reason
          }
          if (Object.keys(link).length > 2) links.push(link)
        }
        return {
          field_key: i.field_key,
          aggregates: i.aggregates,
          ...(i.variant != null ? { variant: i.variant } : {}),
          ...(i.unit != null ? { unit: i.unit } : {}),
          ...(i.region_level != null ? { region_level: i.region_level } : {}),
          links,
        }
      })
    : []
  return {
    request: { file_ids: files, sheets: [...placed].map(refOf), summary: items.length ? { items } : null },
    invalid,
  }
}

/** 지운 파일·시트의 요약 연결 설정을 함께 지운다 (남은 대상의 순서·계산 방식·단위·기간은 그대로) */
function pruneLinks(d: Draft, keep: (k: string) => boolean): Draft {
  return {
    ...d,
    items: d.items.map((i) => ({ ...i, links: Object.fromEntries(Object.entries(i.links).filter(([k]) => keep(k))) })),
  }
}

/** 파일별 시트로 묶기: 고른 파일 → (취합 방식: 파일별 시트) → 시트·통계 요약 설정 → 결과 구성 확인 → 엑셀 만들기.
 * 편집 영역은 마지막으로 받은 확인 결과로 그려 입력 중에 사라지지 않게 하고, 만들기는 지금 구성으로 확인한 결과(basis)가
 * 있고 고칠 입력이 없을 때만 된다. 구성이 바뀌면 다시 확인하고, 늦게 온 확인 응답은 버린다. */
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
  const [descriptions, setDescriptions] = useState<Record<string, string>>({}) // 항목 정의 (단위·산식)

  useEffect(() => {
    loadFields()
      .then((fs) => setDescriptions(Object.fromEntries(fs.map((f) => [f.key, f.description]))))
      .catch(() => undefined) // 정의를 못 읽어도 요약은 그대로 고를 수 있다
  }, [])

  const { request, invalid } = buildRequest(files, draft)
  const reqKey = JSON.stringify(request)
  // fresh: 지금 구성으로 받은 확인 결과 (만들기의 근거). view: 마지막으로 받은 확인 결과 (편집 영역을 그리는 데만 씀)
  const fresh = checked?.key === reqKey ? checked.plan : null
  const view = checked?.plan ?? null
  const stale = files.length > 0 && !fresh && checkError?.key !== reqKey
  const placed = new Set(request.sheets.map(keyOf))

  useEffect(() => {
    saveDraft(filesKey, draft)
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
    if (!fresh) return
    setDraft((d) => {
      const base = (d.picks ?? fresh.sheets.map(keyOf)).filter((k) => files.includes(refOf(k).file_id))
      const add = fresh.files
        .filter((f) => f.sheets.length === 1 && !base.includes(`${f.file_id}:s0`))
        .map((f) => `${f.file_id}:s0`)
      const next = [...base, ...add]
      return d.picks && next.join('|') === d.picks.join('|') ? d : { ...d, picks: next }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fresh])

  function update(fn: (d: Draft) => Draft) {
    setFailure(null)
    setDraft(fn)
  }

  function togglePick(k: string, on: boolean) {
    update((d) => {
      const picks = d.picks ?? []
      const next = { ...d, picks: on ? [...picks.filter((x) => x !== k), k] : picks.filter((x) => x !== k) }
      return on ? next : pruneLinks(next, (x) => x !== k) // 결과에서 뺀 시트의 요약 설정도 지운다
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
    // 남은 파일의 설정은 이어 쓰고, 뺀 파일의 시트·요약 연결 설정은 지운다
    const gone = (k: string) => refOf(k).file_id === fid
    const next = pruneLinks({ ...draft, picks: draft.picks?.filter((k) => !gone(k)) ?? null }, (k) => !gone(k))
    saveDraft(rest.join(','), next)
    replaceSearch(q.toString() ? `?${q.toString()}` : '')
  }

  function setItem(index: number, fn: (i: ItemDraft) => ItemDraft) {
    update((d) => ({ ...d, items: d.items.map((it, n) => (n === index ? fn(it) : it)) }))
  }

  function setLink(index: number, k: string, patch: Partial<LinkDraft>) {
    setItem(index, (it) => ({ ...it, links: { ...it.links, [k]: { ...it.links[k], ...patch } } }))
  }

  async function make() {
    if (!fresh || !fresh.ready || invalid.length || running) return
    setRunning(true)
    setFailure(null)
    try {
      setMade({ key: reqKey, run: await runBundle(request, fresh.basis) })
    } catch (err) {
      setFailure(
        err instanceof AnalysisProblemError
          ? { message: err.message, problems: err.problems }
          : { message: outcomeText(err, ''), problems: [], unknown: outcomeUnknown(err) },
      )
      setVersion((v) => v + 1) // 지금 상태로 다시 확인
    } finally {
      setRunning(false)
    }
  }

  const sheetName = (k: string) => view?.sheets.find((s) => keyOf(s) === k)?.name ?? `#${refOf(k).file_id} ${refOf(k).sheet_id}`
  const summaryEmpty = draft.summary && draft.items.length === 0
  let why: string | null = null
  if (!files.length) why = '고른 파일이 없습니다 — 홈에서 파일을 고르세요.'
  else if (invalid.length)
    why = `고칠 입력이 있습니다 — '${sheetName(invalid[0].key)}' 기간 '${invalid[0].text}'은(는) 2026 또는 2026-09 형식이 아닙니다.`
  else if (checkError?.key === reqKey) why = '결과 구성을 확인하지 못했습니다 — 아래 오류를 확인하세요.'
  else if (!fresh) why = '바꾼 구성을 확인하는 중입니다 (확인이 끝나야 만들 수 있음).'
  else if (summaryEmpty) why = "통계 요약 항목을 하나 이상 추가하거나 '통계 요약 시트 추가'를 끄세요."
  else if (fresh.problems.length) why = `만들 수 없는 이유 ${fresh.problems.length}건을 먼저 해결하세요 (위 목록).`
  else if (!fresh.ready) why = '결과에 넣을 시트가 없습니다.'

  const steps: Step[] = [
    { key: 'files', state: files.length ? 'done' : 'todo', detail: `${files.length}개`, href: `/?files=${filesKey}` },
    { key: 'mode', state: 'done', detail: '파일별 시트', href: '/' },
    {
      key: 'settings',
      state: !fresh ? 'todo' : fresh.problems.length || summaryEmpty || invalid.length ? 'attention' : 'done',
      detail: draft.summary ? '시트·통계 요약' : '시트 (통계 요약 없음)',
      current: true,
    },
    { key: 'compose', state: fresh ? (why ? 'attention' : 'done') : 'todo', detail: fresh ? `시트 ${fresh.sheet_count}개` : undefined },
    { key: 'make', state: made?.key === reqKey ? 'done' : 'todo', detail: made?.key === reqKey ? `실행 #${made.run.id}` : undefined },
  ]
  const shownFiles = view ? view.files.filter((f) => files.includes(f.file_id)) : []
  const multi = shownFiles.filter((f) => f.sheets.length > 1)
  const usedFields = new Set(draft.items.map((i) => i.field_key))
  const addable = view ? view.fields.filter((f) => !usedFields.has(f.key)) : []
  const pickedField = addable.some((f) => f.key === newField) ? newField : (addable[0]?.key ?? '')
  const shownSheets = view ? view.sheets.filter((s) => placed.has(keyOf(s))) : []

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
            {files.map((fid) => {
              const f = shownFiles.find((x) => x.file_id === fid)
              if (!f)
                return (
                  <li key={fid}>
                    <span className="bd-file-name">#{fid}</span>
                    <span className="mw-muted">확인 중…</span>
                    <button type="button" className="login-link" onClick={() => removeFile(fid)}>
                      이 파일 빼기
                    </button>
                  </li>
                )
              return (
                <li key={f.file_id} className={f.problems.length ? 'has-problem' : undefined}>
                  <span className="bd-file-name">
                    #{f.file_id} {f.file_name}
                  </span>
                  <span className="mw-muted">
                    시트 {f.sheets.length}개{f.skipped_sheets.length > 0 && ` · 설명 시트 ${f.skipped_sheets.join(', ')}는 앱이 읽지 않아 넣지 않음`}
                  </span>
                  <button
                    type="button"
                    className="login-link"
                    title="결과에서 이 파일을 빼고, 이 파일에 둔 통계 요약 설정도 지웁니다"
                    onClick={() => removeFile(f.file_id)}
                  >
                    이 파일 빼기
                  </button>
                  {f.pii === 'needs_ack' && f.pii_summary && (
                    <PiiNotice fileId={f.file_id} pii={f.pii_summary} onAcknowledged={() => setVersion((v) => v + 1)} />
                  )}
                </li>
              )
            })}
          </ul>
          <p className="mw-muted">
            파일을 더 고르려면 <NavLink href={`/?files=${filesKey}`}>홈의 파일 목록</NavLink>으로 돌아가세요 (최대 {view?.limits.files ?? 50}개).
          </p>
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>2. 필요한 설정</legend>
          <h3 className="bd-sub">넣을 시트</h3>
          {view && multi.length === 0 && <p className="mw-muted">고른 파일이 모두 시트 하나라 그대로 넣습니다 (파일마다 결과 시트 하나).</p>}
          {multi.length > 0 && (
            <p className="mw-muted">
              결과 파일에 넣을 시트를 고르세요. 빼면 그 시트의 통계 요약 설정도 지웁니다. 시트는 넣고 어떤 항목의 계산에서만 빼려면
              아래 요약 표의 &lsquo;이 항목 계산에 포함&rsquo;을 끄세요.
            </p>
          )}
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
                않음). 계산은 데이터 통계 분석과 같은 규칙입니다 — 평균은 유효한 숫자의 합 ÷ 개수, 빈칸은 0이 아니라 뺍니다. 요약은 만든
                때의 값이며 자동으로 다시 계산되지 않습니다. 한 항목에 시트 {view?.limits.summary_sheets ?? 20}개까지 계산합니다
                (데이터 통계 분석과 같은 한도).
              </p>
              {draft.items.map((it, index) => (
                <SummaryItem
                  key={it.field_key}
                  draft={it}
                  out={view?.summary?.items.find((x) => x.field_key === it.field_key) ?? null}
                  checking={stale}
                  placed={placed}
                  invalid={invalid.filter((x) => x.field_key === it.field_key).map((x) => x.key)}
                  onItem={(patch) => setItem(index, (x) => ({ ...x, ...patch }))}
                  onLink={(k, patch) => setLink(index, k, patch)}
                  onReset={() => setItem(index, (x) => ({ field_key: x.field_key, aggregates: x.aggregates, links: {} }))}
                  onRemove={() => update((d) => ({ ...d, items: d.items.filter((_, n) => n !== index) }))}
                />
              ))}
              {view && view.fields.length === 0 && (
                <p className="wk-why" role="note">
                  <strong>추가할 항목 없음</strong> 고른 시트에 확정한 수량 항목이 없습니다 — 열 매핑에서 확정하면 요약에 쓸 수 있습니다
                  (요약 없이 묶기는 그대로 됩니다).
                </p>
              )}
              {addable.length > 0 && draft.items.length < (view?.limits.items ?? 10) && (
                <div className="bd-add">
                  <label>
                    요약할 항목
                    <select
                      value={pickedField}
                      onChange={(e) => setNewField(e.target.value)}
                      aria-describedby={descriptions[pickedField] ? 'bd-field-help' : undefined}
                    >
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
              {addable.length > 0 && draft.items.length < (view?.limits.items ?? 10) && descriptions[pickedField] && (
                <p id="bd-field-help" className="mw-muted">
                  항목 정의 — {descriptions[pickedField]}
                </p>
              )}
            </div>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step" aria-busy={stale}>
          <legend>3. 결과 구성 확인</legend>
          {checkError?.key === reqKey && (
            <div className="upload-error" role="alert">
              <p>결과 구성을 확인하지 못했습니다: {checkError.message}</p>
              <p>위 설정을 고치면 다시 확인합니다 (새로고침하거나 항목을 지울 필요 없음).</p>
            </div>
          )}
          {stale && (
            <p className="mw-muted bd-checking" role="status">
              {view ? '바꾼 구성을 확인하는 중… 아래는 직전 확인 결과입니다 (만들기의 근거로 쓰지 않음).' : '지금 구성을 확인하는 중…'}
            </p>
          )}
          {view && (
            <div className={stale ? 'bd-stale' : undefined}>
              <p className="bd-total" role="status">
                결과 시트 <strong>{view.sheet_count}</strong>개 · 넣을 행 <strong>{view.total_rows.toLocaleString('ko-KR')}</strong>행 (한 번에{' '}
                {view.limits.rows.toLocaleString('ko-KR')}행 · 시트 {view.limits.sheets}개까지 — 넘으면 일부만 넣지 않고 만들지 않음)
              </p>
              {view.problems.length > 0 && (
                <div className="upload-error bd-problems" role="alert">
                  <p>
                    <strong>만들 수 없는 이유 {view.problems.length}건</strong>
                  </p>
                  <ul>
                    {view.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}
              {view.warnings.length > 0 && (
                <ul className="mapping-warn bd-warnings">
                  {view.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              )}
              <ol className="bd-sheets" aria-label="결과 시트 순서">
                {shownSheets.map((s, i) => (
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
                        disabled={i === shownSheets.length - 1}
                        onClick={() => move(keyOf(s), 1)}
                      >
                        아래로
                      </button>
                    </div>
                  </li>
                ))}
                {view.summary && (
                  <li className="is-summary">
                    <span className="bd-sheet-no">{shownSheets.length + 1}</span>
                    <div className="bd-sheet-body">
                      <p className="bd-sheet-name">
                        <strong>{view.summary.sheet_name}</strong> <span className="mw-muted">(항상 마지막)</span>
                      </p>
                      <p className="mw-muted">
                        항목{' '}
                        {view.summary.items
                          .map(
                            (i) =>
                              `${i.field_label}${i.source_columns.length ? ` — 원본 열 ${i.source_columns.map((c) => c.column).join(', ')}` : ''} (${i.aggregate_labels.join('·')}, ${i.coverage.text})`,
                          )
                          .join(' · ')}{' '}
                        — 만든
                        때의 값, 자동으로 다시 계산되지 않음
                      </p>
                    </div>
                  </li>
                )}
              </ol>
            </div>
          )}
        </fieldset>
      )}

      {files.length > 0 && (
        <fieldset className="an-step bd-step">
          <legend>4. 엑셀 만들기</legend>
          <div className="admin-actions">
            <button type="button" className="mapping-run" disabled={why !== null || running} onClick={() => void make()}>
              {running ? '만드는 중…' : `엑셀 만들기 (시트 ${(fresh ?? view)?.sheet_count ?? '-'}개)`}
            </button>
          </div>
          {why && !running && (
            <p className="wk-why" role="note">
              <strong>누를 수 없음</strong> {why}
            </p>
          )}
          {failure && (
            <div className={failure.unknown ? 'mapping-warn' : 'upload-error'} role={failure.unknown ? 'status' : 'alert'}>
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
                <RunDownloadLink runId={made.run.id}>내려받기</RunDownloadLink> ·{' '}
                <a
                  href="/runs"
                  onClick={(e) => {
                    e.preventDefault()
                    navigate('/runs', { focus: { to: 'page' } })
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
  user_excluded: { text: '이 항목에서만 뺌 (시트는 결과에 들어감)', cls: '' },
  unchecked: { text: '확인 전', cls: '' },
}

const LEVELS: { value: Level | null; label: string }[] = [
  { value: 'upper', label: '상위 지역만' },
  { value: 'lower', label: '하위 지역만' },
  { value: null, label: '고르지 않음' },
]

function num(v: unknown) {
  return typeof v === 'number' ? v.toLocaleString('ko-KR') : v == null ? '확인 불가' : String(v)
}

function SummaryItem({
  draft,
  out,
  checking,
  placed,
  invalid,
  onItem,
  onLink,
  onReset,
  onRemove,
}: {
  draft: ItemDraft
  out: BundlePlanItem | null
  checking: boolean
  placed: Set<string>
  invalid: string[]
  onItem: (patch: Partial<ItemDraft>) => void
  onLink: (k: string, patch: Partial<LinkDraft>) => void
  onReset: () => void
  onRemove: () => void
}) {
  const label = out?.field_label ?? draft.field_key
  const variants = out?.variants ?? []
  const units = out?.units ?? []
  const levels = out?.region_levels
  const showVariant = variants.length > 1 || draft.variant != null
  const showUnit = units.length > 1 || draft.unit != null
  const showLevel = (levels?.mismatch ?? 0) > 0 || draft.region_level != null
  const hasSettings =
    Object.keys(draft.links).length > 0 || draft.variant != null || draft.unit != null || draft.region_level != null
  return (
    <div className={`bd-item${out && !out.ready ? ' needs-fix' : ''}`}>
      <div className="bd-item-head">
        <strong>{label}</strong>
        {out && out.source_columns.length > 0 && <span className="mw-muted">원본 열 {sourceText(out.source_columns)}</span>}
        {out && <span className={`upload-tag ${out.ready ? 'mapping-confirmed' : 'mapping-review'}`}>{out.coverage.text}</span>}
        {checking && <span className="mw-muted">확인 중…</span>}
        {out?.unit && <span className="mw-muted">단위 {out.unit}</span>}
        {out && out.periods.length > 0 && <span className="mw-muted">기간 {out.periods.join(', ')}</span>}
        {hasSettings && (
          <button type="button" className="login-link" onClick={onReset} title="이 항목의 계산 조건·연결 설정만 지웁니다 (항목은 남김)">
            설정 지우기
          </button>
        )}
        <button type="button" className="mapping-confirm wk-danger" onClick={onRemove}>
          항목 빼기
        </button>
      </div>
      {out && out.source_columns.length > 1 && <p className="mapping-warn">{SOURCE_NAMES_DIFFER}</p>}
      <div className="bd-aggs" role="group" aria-label={`${label} 계산 방식`}>
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
                  onItem({ aggregates: AGGS.map((x) => x.key).filter((k) => (k === a.key ? e.target.checked : draft.aggregates.includes(k))) })
                }
              />
              {a.label}
            </label>
          )
        })}
      </div>
      {(showVariant || showUnit || showLevel) && (
        <div className="bd-choices" role="group" aria-label={`${label} 계산 조건`}>
          {showVariant && (
            <label className="bd-choice">
              <span className="bd-choice-name">분석할 구분</span>
              <select
                aria-label={`${label} 분석할 구분`}
                value={draft.variant == null ? '' : `v:${draft.variant}`}
                onChange={(e) => onItem({ variant: e.target.value ? e.target.value.slice(2) : null })}
              >
                <option value="">고르세요</option>
                {[...new Set([...variants, ...(draft.variant != null ? [draft.variant] : [])])].map((v) => (
                  <option key={v} value={`v:${v}`}>
                    {v || '구분 없음'}
                  </option>
                ))}
              </select>
              <span className="bd-choice-help">같은 항목이지만 열 이름의 구분이 다른 열이 있습니다 — 고른 구분의 열만 계산합니다.</span>
            </label>
          )}
          {showUnit && (
            <label className="bd-choice">
              <span className="bd-choice-name">계산할 단위</span>
              <select aria-label={`${label} 계산할 단위`} value={draft.unit ?? ''} onChange={(e) => onItem({ unit: e.target.value || null })}>
                <option value="">고르세요</option>
                {[...new Set([...units, ...(draft.unit ? [draft.unit] : [])])].map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
              <span className="bd-choice-help">
                고른 단위의 자료만 계산합니다 (자동 환산 없음). 다른 단위 자료는 아래 표에서 &lsquo;이 항목 계산에 포함&rsquo;을 꺼
                뺀다고 확인해야 만들 수 있습니다.
              </span>
            </label>
          )}
          {showLevel && (
            <fieldset className="bd-choice">
              <legend className="bd-choice-name">{label} 집계 수준 (상위·하위 지역)</legend>
              <div className="bd-level-options">
                {LEVELS.map((l) => (
                  <label key={l.label}>
                    <input
                      type="radio"
                      name={`bd-level-${draft.field_key}`}
                      checked={(draft.region_level ?? null) === l.value}
                      onChange={() => onItem({ region_level: l.value })}
                    />
                    {l.label}
                  </label>
                ))}
              </div>
              {levels && levels.conflicts.length > 0 && (
                <ul className="bd-levels">
                  {levels.conflicts.map((x, i) => (
                    <li key={i}>
                      {x.period ?? '-'} {x.upper.target}: 상위 값 {num(x.upper.value)} (#{x.upper.file_id} {x.upper.row}행) · 하위 지역{' '}
                      {x.lower_count}곳 합계 {num(x.lower_sum)}
                      {x.lower_missing ? ` (값 없는 하위 지역 ${x.lower_missing}곳)` : ''} · 차이 {num(x.difference)}
                    </li>
                  ))}
                </ul>
              )}
              <span className="bd-choice-help">
                하위 지역 합계가 상위 지역 값과 다릅니다. 상위만 고르면 하위 지역 행을, 하위만 고르면 상위 지역 행을 뺍니다. 기준일이 다른
                자료는 이 선택으로 함께 계산하지 않습니다.
              </span>
            </fieldset>
          )}
        </div>
      )}
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
                <th scope="col">이 항목 계산에 포함</th>
                <th scope="col">상태·사유</th>
              </tr>
            </thead>
            <tbody>
              {out.links
                .filter((lk) => placed.has(keyOf(lk)))
                .map((lk) => {
                  const k = keyOf(lk)
                  const d = draft.links[k] ?? {}
                  const needUnit = lk.reasons.some((r) => r.startsWith('단위를 확인할 수 없음'))
                  const needPeriod = lk.reasons.some((r) => r.startsWith('기간을 확인할 수 없음'))
                  const pickColumn = (lk.candidates?.length ?? 0) > 1 && lk.reasons.some((r) => r.includes('분석할 열을 고르세요'))
                  const period = periodOf(d.period_input)
                  const badPeriod = period.bad || invalid.includes(k)
                  const errId = `bd-period-error-${k.replace(':', '-')}`
                  return (
                    <tr key={k} className={`is-${lk.status}`}>
                      <th scope="row">{lk.output_sheet}</th>
                      <td>
                        #{lk.file_id} {lk.file_name} / {lk.sheet}
                      </td>
                      <td>
                        {pickColumn || d.column_id ? (
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
                        {needUnit || d.unit_input !== undefined ? (
                          <>
                            <input
                              type="text"
                              className="bd-input"
                              aria-label={`${lk.output_sheet} 단위`}
                              placeholder="예: 명"
                              value={d.unit_input ?? ''}
                              maxLength={20}
                              onChange={(e) => onLink(k, { unit_input: e.target.value })}
                            />
                            {lk.unit && (
                              <span className="bd-reason">
                                확인된 단위 {lk.unit} ({lk.unit_source ?? '-'})
                              </span>
                            )}
                          </>
                        ) : lk.unit ? (
                          `${lk.unit} (${lk.unit_source ?? '-'})`
                        ) : (
                          '-'
                        )}
                      </td>
                      <td>
                        {needPeriod || d.period_input !== undefined ? (
                          <>
                            <input
                              type="text"
                              className="bd-input"
                              aria-label={`${lk.output_sheet} 기간`}
                              placeholder="2026-09"
                              inputMode="numeric"
                              value={d.period_input ?? ''}
                              aria-invalid={badPeriod}
                              aria-describedby={badPeriod ? errId : undefined}
                              onChange={(e) => onLink(k, { period_input: e.target.value })}
                            />
                            {badPeriod && (
                              <span id={errId} className="bd-input-error" aria-live="polite">
                                형식이 맞지 않습니다 — 2026 또는 2026-09처럼 적으세요 (고치기 전에는 만들 수 없음)
                              </span>
                            )}
                            {lk.period && (
                              <span className="bd-reason">
                                확인된 기간 {lk.period} ({lk.period_source ?? '-'})
                              </span>
                            )}
                          </>
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
                          <input type="checkbox" checked={!d.exclude} onChange={(e) => onLink(k, { exclude: !e.target.checked })} />
                          포함
                        </label>
                        {d.exclude && (
                          <input
                            type="text"
                            className="bd-input"
                            aria-label={`${lk.output_sheet} 뺀 이유`}
                            placeholder="뺀 이유 (선택)"
                            maxLength={100}
                            value={d.exclude_reason_input ?? ''}
                            onChange={(e) => onLink(k, { exclude_reason_input: e.target.value })}
                          />
                        )}
                      </td>
                      <td>
                        <span className={`upload-tag ${LINK_STATE[lk.status].cls}`}>{LINK_STATE[lk.status].text}</span>
                        {(lk.exclude_reason || lk.reasons.length > 0) && (
                          <span className="bd-reason">{lk.exclude_reason ?? lk.reasons.join('; ')}</span>
                        )}
                        {/* 합계 항목의 산식 확인 — 불일치는 위 사유에 적힘 (다시 확정하거나 '포함'을 꺼야 계산) */}
                        {lk.formula && lk.formula.state !== 'mismatch' && <span className="bd-reason">{lk.formula.text}</span>}
                        {(lk.repeated || d.rows_are_records) && (
                          <label className="bd-records">
                            <input
                              type="checkbox"
                              aria-label={`${lk.output_sheet} 반복 행 모두 더함`}
                              checked={!!d.rows_are_records}
                              onChange={(e) => onLink(k, { rows_are_records: e.target.checked })}
                            />
                            서로 다른 기록 — 반복 행 모두 더함
                            {lk.repeated && (
                              <span className="bd-reason">
                                같은 대상 {lk.repeated.keys}개 · {lk.repeated.rows}행 (값 같음 {lk.repeated.same_value}, 다름{' '}
                                {lk.repeated.different_value}). 다른 파일과의 중복은 이 확인으로 풀리지 않습니다.
                              </span>
                            )}
                          </label>
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
