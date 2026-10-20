import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import {
  analysisDownloadUrl,
  AnalysisProblemError,
  fetchAnalysisFields,
  fetchAnalysisPage,
  fetchAnalysisRun,
  fetchAnalysisRuns,
  fetchAnalysisSources,
  previewAnalysis,
  runAnalysis,
  type AggKey,
  type AnalysisField,
  type AnalysisGroup,
  type AnalysisPage,
  type AnalysisPreview,
  type AnalysisRequest,
  type AnalysisRowRef,
  type AnalysisRunDetail,
  type AnalysisRunSummary,
  type AnalysisSource,
  type AnalysisTarget,
  type AnalysisTargetIn,
  type RegionLevel,
} from './api'
import { loadFields } from './mappingText'
import { navigate } from './router'
import { formatTime } from './runFormat'

const AGGS: AggKey[] = ['sum', 'mean', 'min', 'max', 'count']
const AGG_LABEL: Record<AggKey, string> = {
  sum: '합계',
  mean: '평균',
  min: '최솟값',
  max: '최댓값',
  count: '유효 숫자 개수',
}
const NO_VALUE = '계산할 값 없음'
const PAGE = 50
const PERIOD_RE = /^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$/
const STATUS_LABEL: Record<AnalysisTarget['status'], string> = {
  ok: '사용',
  excluded: '제외',
  needs_input: '확인 필요',
}

function useSearch(): string {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('popstate', cb)
      window.addEventListener('moajwo:navigate', cb)
      return () => {
        window.removeEventListener('popstate', cb)
        window.removeEventListener('moajwo:navigate', cb)
      }
    },
    () => window.location.search,
    () => '',
  )
}

function fmt(v: number | null | undefined): string {
  return v === null || v === undefined ? NO_VALUE : v.toLocaleString('ko-KR', { maximumFractionDigits: 4 })
}

/** 상위·하위 비교 표시용: 반올림하지 않는다 (작은 차이가 0으로 보이지 않게) */
function exactText(v: number | null): string {
  return v === null ? '확인 불가' : v.toLocaleString('ko-KR', { maximumFractionDigits: 20 })
}

function showValue(v: number | string | null): string {
  return typeof v === 'number' ? exactText(v) : String(v ?? '(빈칸)')
}

type TargetSetting = { include: boolean; unit: string; period: string; column: string; records: boolean }
const NEW_SETTING: TargetSetting = { include: true, unit: '', period: '', column: '', records: false }
const LEVEL_LABEL: Record<RegionLevel, string> = {
  lower: '하위 지역만 (상위 지역 행을 빼고 있는 하위 지역만 더함)',
  upper: '상위 지역만 (상위 지역과 함께 있는 하위 지역 행을 뺌)',
}
type BinDraft = { label: string; min: string; max: string; minIn: boolean; maxIn: boolean }
const targetKey = (t: { file_id: number; sheet_id: string | null }) => `${t.file_id}:${t.sheet_id ?? ''}`
const toNum = (s: string) => (s.trim() === '' ? null : Number(s))

/** 데이터 분석: 주소에 id가 있으면 저장된 결과, 없으면 새 분석 만들기 */
function Analysis() {
  const search = useSearch()
  const q = new URLSearchParams(search)
  const id = Number(q.get('id'))
  if (Number.isInteger(id) && id > 0) return <AnalysisResult key={id} id={id} />
  const files = (q.get('files') ?? '')
    .split(',')
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)
  return <AnalysisBuilder key={search} initialField={q.get('field') ?? ''} initialFiles={files} />
}

function binOverlap(a: BinDraft, b: BinDraft): boolean {
  const lo = (d: BinDraft) => toNum(d.min) ?? -Infinity
  const hi = (d: BinDraft) => toNum(d.max) ?? Infinity
  const [aLo, bLo, aHi, bHi] = [lo(a), lo(b), hi(a), hi(b)]
  const low = aLo !== bLo ? (aLo > bLo ? [aLo, a.minIn] : [bLo, b.minIn]) : [aLo, a.minIn && b.minIn]
  const high = aHi !== bHi ? (aHi < bHi ? [aHi, a.maxIn] : [bHi, b.maxIn]) : [aHi, a.maxIn && b.maxIn]
  return (low[0] as number) < (high[0] as number) || (low[0] === high[0] && Boolean(low[1]) && Boolean(high[1]))
}

function AnalysisBuilder({ initialField, initialFiles }: { initialField: string; initialFiles: number[] }) {
  const [fields, setFields] = useState<AnalysisField[] | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({ period: '기간' })
  const [field, setField] = useState(initialField)
  const [sources, setSources] = useState<AnalysisSource[] | null>(null)
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(initialFiles))
  const [preview, setPreview] = useState<AnalysisPreview | null>(null)
  const [settings, setSettings] = useState<Record<string, TargetSetting>>({})
  const [variant, setVariant] = useState<string | null>(null)
  const [unit, setUnit] = useState<string | null>(null)
  const [groupBy, setGroupBy] = useState<string[]>([])
  const [dimFilters, setDimFilters] = useState<Record<string, string[]>>({})
  const [periodFrom, setPeriodFrom] = useState('')
  const [periodTo, setPeriodTo] = useState('')
  const [valueMin, setValueMin] = useState('')
  const [valueMax, setValueMax] = useState('')
  const [aggs, setAggs] = useState<AggKey[]>(AGGS)
  const [useBins, setUseBins] = useState(false)
  const [binOn, setBinOn] = useState<AggKey>('sum')
  const [bins, setBins] = useState<BinDraft[]>([
    { label: '', min: '', max: '', minIn: true, maxIn: false },
    { label: '', min: '', max: '', minIn: true, maxIn: false },
  ])
  const [regionLevel, setRegionLevel] = useState<RegionLevel | null>(null)
  // 대상 확인을 보낸 조건과 그 요청 본문. 지금 조건과 같을 때만 실행할 수 있고, 실행은 이 본문 그대로 보낸다
  const [checked, setChecked] = useState<{ key: string; body: AnalysisRequest } | null>(null)
  const [stale, setStale] = useState(false)
  const seq = useRef(0)
  const keyRef = useRef('')
  const [busy, setBusy] = useState<'preview' | 'run' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [problems, setProblems] = useState<string[]>([])

  useEffect(() => {
    fetchAnalysisFields()
      .then(setFields)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    loadFields()
      .then((fs) => setLabels(Object.fromEntries([['period', '기간'], ...fs.map((f) => [f.key, f.label])])))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!field) return
    let alive = true
    fetchAnalysisSources(field)
      .then((s) => alive && setSources(s))
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      alive = false
    }
  }, [field])

  function pickField(key: string) {
    setField(key)
    setSources(null)
    setPreview(null)
    setSettings({})
    setVariant(null)
    setUnit(null)
    setRegionLevel(null)
    setChecked(null)
    setGroupBy([])
    setDimFilters({})
    setError(null)
    setProblems([])
  }

  const binError = useMemo(() => {
    if (!useBins) return null
    for (const b of bins) {
      if (toNum(b.min) === null && toNum(b.max) === null) return '구간마다 아래 경계나 위 경계 중 하나는 적어야 합니다.'
      if ([b.min, b.max].some((s) => s.trim() !== '' && Number.isNaN(Number(s)))) return '경계는 숫자로 적으세요.'
    }
    for (let i = 0; i < bins.length; i++)
      for (let j = i + 1; j < bins.length; j++)
        if (binOverlap(bins[i], bins[j])) return `${i + 1}번째와 ${j + 1}번째 구간이 겹칩니다.`
    return null
  }, [bins, useBins])

  function request(): AnalysisRequest {
    const targets: AnalysisTargetIn[] = preview
      ? preview.targets
          .filter((t) => settings[targetKey(t)]?.include ?? true)
          .map((t) => {
            if (t.sheet_id === null) return { file_id: t.file_id }
            const s = settings[targetKey(t)]
            return {
              file_id: t.file_id,
              sheet_id: t.sheet_id,
              column_id: s?.column || null,
              unit: s?.unit.trim() || null,
              period: s?.period.trim() || null,
              rows_are_records: s?.records ?? false,
            }
          })
      : [...chosen].map((file_id) => ({ file_id }))
    return {
      field_key: field,
      variant,
      unit,
      targets,
      group_by: groupBy,
      filters: {
        dims: Object.fromEntries(Object.entries(dimFilters).filter(([, v]) => v.length > 0)),
        period_from: periodFrom || null,
        period_to: periodTo || null,
        value_min: toNum(valueMin),
        value_max: toNum(valueMax),
      },
      aggregates: aggs,
      bins: useBins
        ? {
            on: binOn,
            ranges: bins.map((b) => ({
              label: b.label.trim() || null,
              min: toNum(b.min),
              max: toNum(b.max),
              min_inclusive: b.minIn,
              max_inclusive: b.maxIn,
            })),
          }
        : null,
      region_level: regionLevel,
    }
  }

  // 지금 화면의 조건 (대상 확인 응답이 이 조건에 대한 것인지 비교하는 데 쓴다)
  const conditionKey = JSON.stringify({
    field,
    files: [...chosen].sort((a, b) => a - b),
    settings: Object.entries(settings)
      .filter(([, v]) => JSON.stringify(v) !== JSON.stringify(NEW_SETTING))
      .sort(([a], [b]) => a.localeCompare(b)),
    variant,
    unit,
    regionLevel,
    groupBy,
    dims: Object.entries(dimFilters)
      .filter(([, v]) => v.length > 0)
      .sort(([a], [b]) => a.localeCompare(b)),
    periodFrom,
    periodTo,
    valueMin,
    valueMax,
    aggs,
    bins: useBins ? { binOn, bins } : null,
  })
  useEffect(() => {
    keyRef.current = conditionKey
  }, [conditionKey])
  const dirty = preview !== null && checked?.key !== conditionKey

  async function check() {
    if (busy || !field || binError) return
    const body = request()
    const key = conditionKey
    const mine = ++seq.current
    setBusy('preview')
    setError(null)
    setProblems([])
    setStale(false)
    try {
      const res = await previewAnalysis(body)
      // 응답을 기다리는 동안 항목·파일·조건이 바뀌었으면 이전 조건의 응답이므로 버린다 (현재 선택을 덮어쓰지 않음)
      if (mine !== seq.current || key !== keyRef.current) {
        setStale(true)
        return
      }
      setPreview(res)
      setChecked({ key, body })
      setSettings((prev) => {
        const next: Record<string, TargetSetting> = {}
        for (const t of res.targets) {
          const k = targetKey(t)
          next[k] = prev[k] ?? NEW_SETTING
        }
        return next
      })
    } catch (err) {
      if (mine !== seq.current || key !== keyRef.current) {
        setStale(true)
        return
      }
      setError(err instanceof Error ? err.message : String(err))
      if (err instanceof AnalysisProblemError) setProblems(err.problems)
    } finally {
      setBusy(null)
    }
  }

  async function run() {
    if (!preview || !checked || busy || !canRun) return
    setBusy('run')
    setError(null)
    setProblems([])
    try {
      const { id } = await runAnalysis(checked.body, preview.fingerprint)
      navigate(`/analysis?id=${id}`, { focus: { to: 'detail' } }) // 실행 뒤 결과의 제목(열지 못하면 오류 안내)으로
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      if (err instanceof AnalysisProblemError) {
        setProblems(err.problems)
        setChecked(null)
      }
    } finally {
      setBusy(null)
    }
  }

  const included = preview ? preview.targets.filter((t) => settings[targetKey(t)]?.include ?? true) : []
  const waiting = included.filter((t) => t.status === 'needs_input')
  const canRun = Boolean(
    preview &&
    checked?.key === conditionKey &&
    !binError &&
    preview.problems.length === 0 &&
    waiting.length === 0 &&
    included.some((t) => t.status === 'ok') &&
    aggs.length > 0,
  )
  const dims = preview ? preview.available_groups.filter((k) => k !== 'period') : []

  function setTarget(t: AnalysisTarget, patch: Partial<TargetSetting>) {
    setSettings((prev) => ({ ...prev, [targetKey(t)]: { ...prev[targetKey(t)], ...patch } }))
  }

  return (
    <section className="an" aria-label="데이터 분석">
      <p className="mw-muted">
        사용자가 확정한 열로 합계·평균·최솟값·최댓값·유효 숫자 개수를 구합니다. 화면을 열거나 대상을 확인해도 다시
        분석하거나 매핑을 바꾸지 않고, 모델을 부르지 않습니다. 계산은 '분석 실행'을 눌렀을 때만 합니다.
      </p>

      <fieldset className="an-step">
        <legend>1. 분석할 항목과 자료</legend>
        <label className="an-field">
          분석할 항목 (사용자가 확정한 수량 항목)
          <select value={field} onChange={(e) => pickField(e.target.value)} aria-label="분석할 항목">
            <option value="">— 항목 고르기 —</option>
            {(fields ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.label} (파일 {f.files}개)
              </option>
            ))}
            {field && fields && !fields.some((f) => f.key === field) && (
              <option value={field}>{labels[field] ?? field} (확정된 내 파일 없음)</option>
            )}
          </select>
        </label>
        {fields?.length === 0 && (
          <p className="mw-muted">확정한 수량 항목이 없습니다. 열 매핑 화면에서 열을 먼저 확정하세요.</p>
        )}
        {/* 자료 목록 불러오기 상태: 영역은 처음부터 두고 내용만 바꾼다 (화면에는 불러오는 동안만 보임) */}
        <p role="status" className={field && sources === null && !error ? 'mw-muted' : 'sr-only'}>
          {!field || error
            ? ''
            : sources === null
              ? '분석할 파일 목록을 불러오는 중…'
              : `분석할 수 있는 파일 ${sources.length}개를 불러왔습니다.`}
        </p>
        {field && (
          <div className="an-files" role="group" aria-label="분석할 파일">
            {sources?.map((s) => (
              <label key={s.file_id} className="an-file">
                <input
                  type="checkbox"
                  checked={chosen.has(s.file_id)}
                  onChange={(e) => {
                    setChosen((prev) => {
                      const next = new Set(prev)
                      if (e.target.checked) next.add(s.file_id)
                      else next.delete(s.file_id)
                      return next
                    })
                    setPreview(null)
                  }}
                />
                <span>
                  #{s.file_id} {s.file_name}
                  <span className="mw-muted mw-block">
                    확정한 시트: {s.sheets.join(', ')} · 열 {s.columns}개
                  </span>
                </span>
              </label>
            ))}
            {[...chosen]
              .filter((id) => sources && !sources.some((s) => s.file_id === id))
              .map((id) => (
                <p key={id} className="mw-muted">
                  #{id} — 이 항목으로 확정된 열이 보이지 않습니다. 대상 확인에서 사유를 보여 줍니다.
                </p>
              ))}
            {sources?.length === 0 && <p className="mw-muted">이 항목으로 확정한 열이 있는 내 파일이 없습니다.</p>}
          </div>
        )}
        <div className="admin-actions">
          <button
            type="button"
            className="mapping-run"
            disabled={!field || chosen.size === 0 || busy !== null || Boolean(binError)}
            onClick={() => void check()}
          >
            {busy === 'preview' ? '확인 중…' : preview ? '다시 확인 (계산 안 함)' : '대상 확인 (계산 안 함)'}
          </button>
        </div>
        {busy === null && (!field || chosen.size === 0) && (
          <p className="wk-why" role="note">
            <strong>누를 수 없음</strong> {!field ? '분석할 항목을 고르세요.' : '분석할 파일을 하나 이상 고르세요.'}
          </p>
        )}
      </fieldset>

      {stale && (
        <p className="mapping-warn" role="status">
          대상 확인 중에 항목·파일·조건이 바뀌어 이전 조건의 확인 결과를 쓰지 않았습니다 — 지금 조건으로 '다시 확인'을
          누르세요.
        </p>
      )}
      {error && (
        <div className="upload-error" role="alert">
          <p>{error}</p>
          {problems.length > 0 && (
            <ul>
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {preview && (
        <>
          <fieldset className="an-step">
            <legend>2. 대상·단위·기간 확인</legend>
            <p className="mw-muted">
              {preview.field_label}
              {preview.stock &&
                ' — 시점 값 항목이라 기간끼리 더하지 않습니다 (기간이 여럿이면 기간별로 나눔).'} 단위{' '}
              {preview.unit ?? '확인 필요'} · 기간 {preview.periods.join(', ') || '확인 필요'} · 계산에 들어갈 행{' '}
              {preview.rows.candidate.toLocaleString('ko-KR')}
            </p>
            {preview.problems.length > 0 && (
              <div className="an-problems" role="alert">
                <strong>실행 전에 해결할 문제</strong>
                <ul>
                  {preview.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
            {preview.warnings.map((w) => (
              <p key={w} className="mapping-warn">
                {w}
              </p>
            ))}
            {preview.variants.length > 1 && (
              <div role="radiogroup" aria-label="열 이름의 구분" className="an-choice">
                <strong>열 이름의 구분 (같은 항목이라도 구분이 다르면 함께 계산하지 않음)</strong>
                {preview.variants.map((v) => (
                  <label key={v}>
                    <input
                      type="radio"
                      name="variant"
                      checked={variant === v}
                      onChange={() => {
                        setVariant(v)
                      }}
                    />{' '}
                    {v || '구분 없음'}
                  </label>
                ))}
              </div>
            )}
            {(preview.region_levels.conflicts.length > 0 || regionLevel !== null) && (
              <div role="radiogroup" aria-label="지역 집계 수준" className="an-choice">
                <strong>
                  지역 집계 수준 — 하위 지역 합계가 상위 지역 값과 다르거나 확인할 수 없습니다 (하위 지역 일부만 있을 수
                  있음). 같은 기간·같은 구분 안에서 어느 수준으로 더할지 고르세요.
                </strong>
                <ul className="an-level-conflicts">
                  {preview.region_levels.conflicts.map((x, i) => (
                    <li key={i}>
                      {x.period ?? '-'} {x.upper.target}: 상위 값 {showValue(x.upper.value)} (#{x.upper.file_id}{' '}
                      {x.upper.row}행) · 하위 지역 {x.lower_count}곳 합계 {exactText(x.lower_sum)}
                      {x.lower_missing > 0 ? ` (값 없는 하위 지역 ${x.lower_missing}곳)` : ''} · 차이{' '}
                      {exactText(x.difference)}
                    </li>
                  ))}
                </ul>
                {(['lower', 'upper'] as const).map((lv) => (
                  <label key={lv}>
                    <input
                      type="radio"
                      name="region-level"
                      checked={regionLevel === lv}
                      onChange={() => setRegionLevel(lv)}
                    />{' '}
                    {LEVEL_LABEL[lv]}
                  </label>
                ))}
              </div>
            )}
            {preview.units.length > 1 && (
              <div role="radiogroup" aria-label="계산할 단위" className="an-choice">
                <strong>계산할 단위 (자동 환산 없음 — 다른 단위의 자료는 제외)</strong>
                {preview.units.map((u) => (
                  <label key={u}>
                    <input
                      type="radio"
                      name="unit"
                      checked={unit === u}
                      onChange={() => {
                        setUnit(u)
                      }}
                    />{' '}
                    {u}
                  </label>
                ))}
              </div>
            )}
            {preview.overlaps.length > 0 && (
              <div className="an-overlaps">
                <strong>
                  중복 의심 — 같은 대상이 두 자료에 있습니다. 사용할 자료만 남기세요 (자동으로 고르지 않음).
                </strong>
                {preview.overlaps.map((o) => (
                  <div key={`${o.a.file_id}-${o.a.sheet}-${o.b.file_id}-${o.b.sheet}`} className="an-overlap">
                    <p>
                      #{o.a.file_id} {o.a.file_name} ↔ #{o.b.file_id} {o.b.file_name}: {o.count}건 (값 같음{' '}
                      {o.same_value}, 다름 {o.different_value})
                    </p>
                    <ul>
                      {o.samples.map((s, i) => (
                        <li key={i}>
                          {s.period} {s.target}: {o.a.file_name} {s.a.row}행 {String(s.a.value)} / {o.b.file_name}{' '}
                          {s.b.row}행 {String(s.b.value)}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
            <div className="upload-table-wrap">
              <table className="upload-table an-targets">
                <thead>
                  <tr>
                    <th scope="col">사용</th>
                    <th scope="col">파일 · 시트 · 열</th>
                    <th scope="col">단위</th>
                    <th scope="col">기간</th>
                    <th scope="col">상태 · 사유</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.targets.map((t) => {
                    const s = settings[targetKey(t)] ?? NEW_SETTING
                    const needUnit = t.reasons.some((r) => r.startsWith('단위를 확인할 수 없음'))
                    const needPeriod = t.reasons.some((r) => r.startsWith('기간을 확인할 수 없음'))
                    const needColumn = t.reasons.some((r) => r.includes('분석할 열을 고르세요'))
                    return (
                      <tr key={targetKey(t)}>
                        <td data-label="사용">
                          <input
                            type="checkbox"
                            aria-label={`#${t.file_id} ${t.sheet ?? ''} 사용`}
                            checked={s.include}
                            onChange={(e) => setTarget(t, { include: e.target.checked })}
                          />
                        </td>
                        <td data-label="파일 · 시트 · 열">
                          <span className="mw-block">
                            #{t.file_id} {t.file_name}
                          </span>
                          <span className="mw-muted mw-block">
                            {t.sheet ?? '-'} · {t.column ?? '-'}
                            {t.variant ? ` (구분: ${t.variant})` : ''}
                          </span>
                          {needColumn && (
                            <select
                              aria-label={`#${t.file_id} 분석할 열`}
                              value={s.column}
                              onChange={(e) => setTarget(t, { column: e.target.value })}
                            >
                              <option value="">— 열 고르기 —</option>
                              {t.candidates.map((c) => (
                                <option key={c.column_id} value={c.column_id}>
                                  {c.column} {c.variant ? `(${c.variant})` : ''}
                                </option>
                              ))}
                            </select>
                          )}
                        </td>
                        <td data-label="단위">
                          {t.unit ? `${t.unit} (${t.unit_source})` : '-'}
                          {needUnit && (
                            <input
                              className="an-small"
                              aria-label={`#${t.file_id} 단위 확인`}
                              placeholder="예: 명"
                              value={s.unit}
                              onChange={(e) => setTarget(t, { unit: e.target.value })}
                            />
                          )}
                        </td>
                        <td data-label="기간">
                          {t.period
                            ? `${t.period} (${t.period_source})`
                            : t.period_columns.length
                              ? `행의 ${t.period_columns.join('·')} 열`
                              : '-'}
                          {needPeriod && (
                            <input
                              className="an-small"
                              aria-label={`#${t.file_id} 기간 확인`}
                              placeholder="예: 2026-09"
                              value={s.period}
                              onChange={(e) => setTarget(t, { period: e.target.value })}
                              aria-invalid={s.period !== '' && !PERIOD_RE.test(s.period)}
                            />
                          )}
                        </td>
                        <td data-label="상태 · 사유">
                          {t.repeated && (
                            <div className="an-repeated">
                              <span className="mw-block">
                                같은 대상이 반복된 행: {t.repeated.keys}개 대상 {t.repeated.rows}행 (값 같음{' '}
                                {t.repeated.same_value}, 다름 {t.repeated.different_value})
                              </span>
                              {t.repeated.samples.map((x, i) => (
                                <span key={i} className="mw-muted mw-block">
                                  {x.period ?? '-'} {x.target}: {x.rows.join('·')}행 — {x.values.map((v) => String(v ?? '(빈칸)')).join(' / ')}
                                </span>
                              ))}
                              <label>
                                <input
                                  type="checkbox"
                                  checked={s.records}
                                  onChange={(e) => setTarget(t, { records: e.target.checked })}
                                />{' '}
                                반복 행은 서로 다른 기록(거래·발생 건) — 모두 더함
                              </label>
                            </div>
                          )}
                          <span className={`upload-tag an-status-${t.status}`}>{STATUS_LABEL[t.status]}</span>
                          {t.reasons.map((r) => (
                            <span key={r} className="mw-muted mw-block">
                              {r}
                            </span>
                          ))}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </fieldset>

          <fieldset className="an-step">
            <legend>3. 그룹·필터·계산 방식</legend>
            <div className="an-choice" role="group" aria-label="그룹 기준">
              <strong>그룹 기준 (최대 3개, 모든 자료에 있는 열만)</strong>
              {preview.available_groups.map((k) => (
                <label key={k}>
                  <input
                    type="checkbox"
                    checked={groupBy.includes(k)}
                    disabled={!groupBy.includes(k) && groupBy.length >= 3}
                    onChange={(e) => {
                      setGroupBy((prev) => (e.target.checked ? [...prev, k] : prev.filter((x) => x !== k)))
                    }}
                  />{' '}
                  {labels[k] ?? k}
                </label>
              ))}
            </div>
            {dims.map((k) => (
              <details key={k} className="an-filter">
                <summary>
                  필터 — {labels[k] ?? k} ({(dimFilters[k] ?? []).length || '전체'})
                </summary>
                <div className="an-values">
                  {(preview.filter_values[k] ?? []).map((v) => (
                    <label key={v}>
                      <input
                        type="checkbox"
                        checked={(dimFilters[k] ?? []).includes(v)}
                        onChange={(e) => {
                          setDimFilters((prev) => {
                            const cur = prev[k] ?? []
                            return { ...prev, [k]: e.target.checked ? [...cur, v] : cur.filter((x) => x !== v) }
                          })
                        }}
                      />{' '}
                      {v}
                    </label>
                  ))}
                </div>
              </details>
            ))}
            <div className="an-row">
              <label>
                기간 시작
                <select
                  value={periodFrom}
                  onChange={(e) => {
                    setPeriodFrom(e.target.value)
                  }}
                >
                  <option value="">처음</option>
                  {preview.periods.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              <label>
                기간 끝
                <select
                  value={periodTo}
                  onChange={(e) => {
                    setPeriodTo(e.target.value)
                  }}
                >
                  <option value="">끝</option>
                  {preview.periods.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </label>
              <label>
                숫자 최소
                <input
                  className="an-small"
                  inputMode="decimal"
                  value={valueMin}
                  onChange={(e) => {
                    setValueMin(e.target.value)
                  }}
                />
              </label>
              <label>
                숫자 최대
                <input
                  className="an-small"
                  inputMode="decimal"
                  value={valueMax}
                  onChange={(e) => {
                    setValueMax(e.target.value)
                  }}
                />
              </label>
            </div>
            <div className="an-choice" role="group" aria-label="계산 방식">
              <strong>계산 방식 (평균 = 유효한 숫자의 합 ÷ 유효 숫자 개수)</strong>
              {AGGS.map((a) => (
                <label key={a}>
                  <input
                    type="checkbox"
                    checked={aggs.includes(a)}
                    onChange={(e) => {
                      setAggs((prev) =>
                        e.target.checked
                          ? AGGS.filter((x) => x === a || prev.includes(x))
                          : prev.filter((x) => x !== a),
                      )
                    }}
                  />{' '}
                  {AGG_LABEL[a]}
                </label>
              ))}
            </div>
            <label className="an-toggle">
              <input
                type="checkbox"
                checked={useBins}
                onChange={(e) => {
                  setUseBins(e.target.checked)
                }}
              />{' '}
              구간 분류 (결과 열의 값을 구간으로 나눔, 겹치는 구간은 받지 않음)
            </label>
            {useBins && (
              <div className="an-bins">
                <label>
                  적용할 결과 열
                  <select
                    value={binOn}
                    onChange={(e) => {
                      setBinOn(e.target.value as AggKey)
                    }}
                  >
                    {aggs.map((a) => (
                      <option key={a} value={a}>
                        {AGG_LABEL[a]}
                      </option>
                    ))}
                  </select>
                </label>
                {bins.map((b, i) => (
                  <div key={i} className="an-bin" role="group" aria-label={`${i + 1}번째 구간`}>
                    <input
                      className="an-small"
                      placeholder="이름 (비우면 자동)"
                      value={b.label}
                      onChange={(e) => {
                        setBins((prev) => prev.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
                      }}
                    />
                    <input
                      className="an-small"
                      inputMode="decimal"
                      placeholder="아래 경계"
                      aria-label={`${i + 1}번째 구간 아래 경계`}
                      value={b.min}
                      onChange={(e) => {
                        setBins((prev) => prev.map((x, j) => (j === i ? { ...x, min: e.target.value } : x)))
                      }}
                    />
                    <label>
                      <input
                        type="checkbox"
                        checked={b.minIn}
                        onChange={(e) => {
                          setBins((prev) => prev.map((x, j) => (j === i ? { ...x, minIn: e.target.checked } : x)))
                        }}
                      />{' '}
                      이상(포함)
                    </label>
                    <input
                      className="an-small"
                      inputMode="decimal"
                      placeholder="위 경계"
                      aria-label={`${i + 1}번째 구간 위 경계`}
                      value={b.max}
                      onChange={(e) => {
                        setBins((prev) => prev.map((x, j) => (j === i ? { ...x, max: e.target.value } : x)))
                      }}
                    />
                    <label>
                      <input
                        type="checkbox"
                        checked={b.maxIn}
                        onChange={(e) => {
                          setBins((prev) => prev.map((x, j) => (j === i ? { ...x, maxIn: e.target.checked } : x)))
                        }}
                      />{' '}
                      이하(포함)
                    </label>
                    <button
                      type="button"
                      className="mapping-confirm"
                      disabled={bins.length <= 1}
                      onClick={() => {
                        setBins((prev) => prev.filter((_, j) => j !== i))
                      }}
                    >
                      빼기
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="mapping-confirm"
                  disabled={bins.length >= 20}
                  onClick={() => {
                    setBins((prev) => [...prev, { label: '', min: '', max: '', minIn: true, maxIn: false }])
                  }}
                >
                  구간 더하기
                </button>
                {binError && (
                  <p className="upload-error" role="alert">
                    {binError}
                  </p>
                )}
                <p className="mw-muted">
                  어느 구간에도 들지 않는 값은 '미분류', 값이 없는 그룹은 '{NO_VALUE}'로 나눕니다.
                </p>
              </div>
            )}
          </fieldset>

          <fieldset className="an-step">
            <legend>4. 실행</legend>
            {dirty && <p className="mapping-warn">조건을 바꿨습니다 — '다시 확인'을 누른 뒤 실행할 수 있습니다.</p>}
            {waiting.length > 0 && (
              <p className="mapping-warn">
                확인이 필요한 자료 {waiting.length}개 — 단위·기간·열을 적고 다시 확인하거나 '사용'을 해제하세요.
              </p>
            )}
            <div className="admin-actions">
              <button
                type="button"
                className="mapping-run"
                disabled={!canRun || busy !== null}
                onClick={() => void run()}
              >
                {busy === 'run' ? '계산 중… (선택한 전체 행)' : '분석 실행'}
              </button>
              <button type="button" className="mapping-confirm" disabled={busy !== null} onClick={() => void check()}>
                다시 확인
              </button>
            </div>
            {/* 조건 변경·확인 필요 자료는 위에서 따로 알린다 — 그 밖의 이유만 여기 */}
            {!canRun && busy === null && !dirty && waiting.length === 0 && (
              <p className="wk-why" role="note">
                <strong>누를 수 없음</strong>{' '}
                {binError
                  ? '3단계 구간 설정을 고치세요.'
                  : preview && preview.problems.length > 0
                    ? '2단계의 실행할 수 없는 이유를 먼저 해결하세요.'
                    : aggs.length === 0
                      ? '3단계에서 계산 방식을 하나 이상 고르세요.'
                      : "사용할 수 있는 자료가 없습니다 — 2단계에서 자료의 '사용'을 켜세요."}
              </p>
            )}
          </fieldset>
        </>
      )}
      <PastRuns />
    </section>
  )
}

function PastRuns() {
  const [runs, setRuns] = useState<AnalysisRunSummary[] | null>(null)
  useEffect(() => {
    fetchAnalysisRuns()
      .then(setRuns)
      .catch(() => setRuns([]))
  }, [])
  if (!runs || runs.length === 0) return null
  return (
    <details className="an-past">
      <summary data-focus-key="analysis-past">내 분석 결과 ({runs.length})</summary>
      <ul>
        {runs.map((r) => (
          <li key={r.id}>
            <a
              href={`/analysis?id=${r.id}`}
              onClick={(e) => {
                e.preventDefault()
                navigate(`/analysis?id=${r.id}`, { focus: { to: 'detail', returnKey: 'analysis-past' } })
              }}
            >
              #{r.id} {r.field_label}
            </a>{' '}
            <span className="mw-muted">
              {formatTime(r.created_at)} · {r.partial ? '부분 집계' : '전체 집계'}
            </span>
          </li>
        ))}
      </ul>
    </details>
  )
}

function Pager({
  page,
  offset,
  setOffset,
}: {
  page: AnalysisPage<unknown> | null
  offset: number
  setOffset: (n: number) => void
}) {
  if (!page || page.total <= PAGE) return null
  return (
    <div className="mw-pager">
      <button
        type="button"
        className="mapping-confirm"
        disabled={offset === 0}
        onClick={() => setOffset(Math.max(0, offset - PAGE))}
      >
        이전 {PAGE}개
      </button>
      <span>
        {offset + 1}~{Math.min(offset + PAGE, page.total)} / {page.total}
      </span>
      <button
        type="button"
        className="mapping-confirm"
        disabled={offset + PAGE >= page.total}
        onClick={() => setOffset(offset + PAGE)}
      >
        다음 {PAGE}개
      </button>
    </div>
  )
}

/** 표 한 쪽을 불러온다. [쪽, 마지막 요청 실패 여부] — 실패를 숨기지 않고 상태 안내·화면에 알린다 */
function usePage<T>(id: number, kind: 'groups' | 'excluded' | 'detail', offset: number) {
  const [page, setPage] = useState<AnalysisPage<T> | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let alive = true
    fetchAnalysisPage<T>(id, kind, offset, PAGE)
      .then((p) => {
        if (!alive) return
        setPage(p)
        setFailed(false)
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [id, kind, offset])
  return [page, failed] as const
}

/** 분석 표의 가로·세로 스크롤 영역. 내용이 실제로 넘칠 때만 이름(region)과 Tab 정지점을 준다 —
 * 칸 안에 조작 요소가 없는 표라 키보드로 스크롤하려면 영역에 초점이 필요하다. 넘치지 않으면(모바일 카드 모양 등) Tab 정지점을 더하지 않는다. */
function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [scrolls, setScrolls] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => setScrolls(el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
    check()
    const observer = new ResizeObserver(check) // 화면 폭·쪽 이동으로 표 크기가 바뀌면 다시 본다
    observer.observe(el)
    if (el.firstElementChild) observer.observe(el.firstElementChild)
    return () => observer.disconnect()
  }, [])
  return (
    <div
      ref={ref}
      className="upload-table-wrap"
      role={scrolls ? 'region' : undefined}
      aria-label={scrolls ? label : undefined}
      tabIndex={scrolls ? 0 : undefined}
    >
      {children}
    </div>
  )
}

/** 표를 불러오지 못했을 때 그 자리에 남기는 글자 (상태 안내도 같은 내용을 전한다) */
function PageError({ failed, what }: { failed: boolean; what: string }) {
  if (!failed) return null
  return <p className="upload-error">{what} 표를 불러오지 못했습니다. 화면을 다시 열어 보세요.</p>
}

function AnalysisResult({ id }: { id: number }) {
  const [detail, setDetail] = useState<AnalysisRunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [gOffset, setGOffset] = useState(0)
  const [eOffset, setEOffset] = useState(0)
  const [rOffset, setROffset] = useState(0)
  const [groups, groupsFailed] = usePage<AnalysisGroup>(id, 'groups', gOffset)
  const [excluded, excludedFailed] = usePage<AnalysisRowRef & { reason: string; value: number | string | null }>(
    id,
    'excluded',
    eOffset,
  )
  const [rows, rowsFailed] = usePage<AnalysisRowRef & { keys: string[]; value: number }>(id, 'detail', rOffset)

  useEffect(() => {
    fetchAnalysisRun(id)
      .then(setDetail)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    loadFields()
      .then((fs) => setLabels(Object.fromEntries(fs.map((x) => [x.key, x.label]))))
      .catch(() => undefined)
  }, [id])

  // 불러오기 상태: 같은 자리의 같은 요소를 계속 두고 내용만 바꾼다. 조건·요약(상세)과 결과·제외·근거 표는 따로 불러오므로
  // 안내는 실제로 불러온 범위만 말한다 (표가 남았거나 실패했는데 다 불러온 것처럼 말하지 않음). 상세 오류는 아래 alert가 전한다
  const tables = [
    ['결과', groups, groupsFailed],
    ['제외', excluded, excludedFailed],
    ['근거 행', rows, rowsFailed],
  ] as const
  const tablesFailed = tables.filter(([, , failed]) => failed).map(([what]) => what)
  const tablesWaiting = tables.some(([, page, failed]) => !page && !failed)
  const loadMessage = error
    ? ''
    : !detail
      ? '분석 결과를 불러오는 중…'
      : tablesFailed.length
        ? `분석 결과 #${detail.id}의 조건·요약을 불러왔습니다. ${tablesFailed.join('·')} 표를 불러오지 못했습니다.`
        : tablesWaiting
          ? `분석 결과 #${detail.id}의 조건·요약을 불러왔습니다. 결과·제외·근거 표를 불러오는 중…`
          : `분석 결과 #${detail.id}을(를) 불러왔습니다 (조건·요약, 결과·제외·근거 표).`
  const loadStatus = (
    <p role="status" className={!detail && !error ? 'mw-muted' : 'sr-only'}>
      {loadMessage}
    </p>
  )
  if (error)
    return (
      <>
        {loadStatus}
        <section className="an" aria-label="분석 결과">
          <p className="upload-error" role="alert">
            {error}
          </p>
        </section>
      </>
    )
  if (!detail) return <>{loadStatus}</>
  const c = detail.conditions
  const r = detail.result
  const reasons = c.reason_labels
  const f = c.filters
  const dimText = Object.entries(f.dims)
    .filter(([, v]) => v.length)
    .map(([k, v]) => `${labels[k] ?? k}: ${v.join(', ')}`)
    .join('; ')
  return (
    <>
      {loadStatus}
      <section className="an" aria-label="분석 결과">
      <h2>
        분석 결과 #{detail.id}{' '}
        <span className={`upload-tag ${r.partial || r.level_mismatch ? 'an-status-needs_input' : 'an-status-ok'}`}>
          {r.partial ? '부분 집계' : r.level_mismatch ? '선택한 지역 수준 집계' : '전체 집계'}
        </span>
      </h2>
      <p className="mw-muted">
        {formatTime(detail.created_at)} · {detail.created_by ?? '-'}
        {detail.read_only && ' · 조회 전용 (다른 사용자의 결과)'} · 저장된 결과입니다 (지금 매핑이 바뀌어도 이 결과는
        바뀌지 않음).
      </p>
      {r.partial && (
        <p className="mapping-warn">
          빈칸·비공개·숫자가 아닌 값 {r.problem_rows.toLocaleString('ko-KR')}개를 0으로 바꾸지 않고 빼고 계산했습니다 —
          유효한 값만의 부분 집계입니다.
        </p>
      )}
      <dl className="an-conditions">
        <dt>분석 항목</dt>
        <dd>
          {c.field_label}
          {c.variant ? ` (구분: ${c.variant})` : ''} · 단위 {c.unit ?? '-'}
        </dd>
        <dt>기간</dt>
        <dd>
          {c.periods.join(', ') || '-'}
          {r.period_split && ' — 기간이 여러 개라 기간별로 나눔 (기간끼리 더하지 않음)'}
        </dd>
        <dt>그룹 기준</dt>
        <dd>{r.group_labels.join(', ') || '없음 (전체 하나)'}</dd>
        <dt>필터</dt>
        <dd>
          {dimText || '지역·기관 전체'} · 기간 {f.period_from ?? '처음'} ~ {f.period_to ?? '끝'} ·{' '}
          {f.value_min === null && f.value_max === null
            ? '숫자 범위 없음'
            : `숫자 ${f.value_min ?? '제한 없음'} ~ ${f.value_max ?? '제한 없음'}`}
        </dd>
        <dt>계산 방식</dt>
        <dd>
          {c.aggregates.map((a) => AGG_LABEL[a]).join(', ')} · 평균의 분모: {c.mean_denominator}
        </dd>
        {c.bins && (
          <>
            <dt>구간 분류</dt>
            <dd>
              {c.bins.on_label} 기준 —{' '}
              {c.bins.ranges
                .map(
                  (b) =>
                    `${b.label} [${b.min ?? '-∞'}${b.min !== null ? (b.min_inclusive ? ' 포함' : ' 제외') : ''} ~ ${b.max ?? '+∞'}${b.max !== null ? (b.max_inclusive ? ' 포함' : ' 제외') : ''}]`,
                )
                .join(' · ')}
            </dd>
          </>
        )}
        <dt>지역 집계 수준</dt>
        <dd>
          {c.region_level
            ? `${LEVEL_LABEL[c.region_level]} — 사용자 선택`
            : '자동 — 하위 지역 합계가 상위 지역 값과 같을 때만 상위 행을 중복으로 뺌'}
          {r.level_mismatch ? ` · 하위 지역 합계가 상위 지역 값과 다른 곳 ${r.level_mismatch}곳` : ''}
          {c.region_level && c.level_conflicts && c.level_conflicts.length > 0 && (
            <ul className="an-level-conflicts">
              {c.level_conflicts.map((x, i) => (
                <li key={i}>
                  {x.period ?? '-'} {x.upper.target}: 상위 값 {showValue(x.upper.value)} · 하위 지역{' '}
                  {x.lower_count}곳 합계 {exactText(x.lower_sum)}
                  {x.lower_missing > 0 ? ` (값 없는 하위 지역 ${x.lower_missing}곳)` : ''} · 차이{' '}
                  {exactText(x.difference)}
                </li>
              ))}
            </ul>
          )}
        </dd>
        {c.base_dates && Object.keys(c.base_dates).length > 0 && (
          <>
            <dt>기준일</dt>
            <dd>
              {Object.entries(c.base_dates)
                .map(([k, v]) => `${k}: ${v}`)
                .join(', ')}
            </dd>
          </>
        )}
        {c.records && c.records.length > 0 && (
          <>
            <dt>반복 행</dt>
            <dd>서로 다른 기록으로 확인해 모두 더함 — {c.records.join(', ')}</dd>
          </>
        )}
        <dt>포함 · 제외</dt>
        <dd>
          포함 행 {r.rows_included.toLocaleString('ko-KR')} · 뺀 행 {r.rows_excluded.toLocaleString('ko-KR')} (자료 문제{' '}
          {r.problem_rows.toLocaleString('ko-KR')}) · 사용한 자료{' '}
          {detail.inputs.filter((t) => t.status === 'ok').length}개
        </dd>
      </dl>
      {c.warnings.map((w, i) => (
        <p key={i} className="mapping-warn">
          {w}
        </p>
      ))}
      <div className="admin-actions">
        <a className="mapping-run mw-link" href={analysisDownloadUrl(detail.id)} download>
          Excel 다운로드 (결과·조건·제외·근거 행)
        </a>
        <a
          className="mapping-confirm mw-link"
          href="/analysis"
          onClick={(e) => {
            e.preventDefault()
            navigate('/analysis', { clearSearch: true, focus: { to: 'return' } }) // 보던 결과(?id=)를 닫고 새 분석으로 — 연 줄(없으면 화면 제목)로 초점
          }}
        >
          새 분석
        </a>
      </div>

      <h3>결과 ({r.groups_total.toLocaleString('ko-KR')}개)</h3>
      <TableRegion label="결과 표">
        <table className="upload-table an-result">
          <thead>
            <tr>
              {r.group_labels.map((l) => (
                <th key={l} scope="col">
                  {l}
                </th>
              ))}
              {r.aggregates.map((a) => (
                <th key={a} scope="col">
                  {a === 'count' ? '유효 숫자 개수 (평균의 분모)' : AGG_LABEL[a]}
                </th>
              ))}
              {c.bins && <th scope="col">구간</th>}
              <th scope="col">상태</th>
              <th scope="col">출처 파일</th>
            </tr>
          </thead>
          <tbody>
            {/* 키는 결과 번호와 목록 안의 위치 — 가린 이름이 서로 같아져도 행이 합쳐지지 않게 */}
            {groups?.items.map((g, i) => (
              <tr key={`${id}-${groups.offset + i}`}>
                {r.group_labels.map((l, i) => (
                  <td key={l} data-label={l}>
                    {g.keys[i] || '(전체)'}
                  </td>
                ))}
                {r.aggregates.map((a) => (
                  <td key={a} data-label={AGG_LABEL[a]} className="an-num">
                    {a === 'count' ? g.count.toLocaleString('ko-KR') : fmt(g[a])}
                  </td>
                ))}
                {c.bins && <td data-label="구간">{g.bin}</td>}
                <td data-label="상태">{g.partial ? `부분 (자료 문제 ${g.problem_rows}행 뺌)` : '전체'}</td>
                <td data-label="출처 파일">{g.files}</td>
              </tr>
            ))}
            {groups && groups.items.length === 0 && (
              <tr>
                <td colSpan={99}>포함된 행이 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TableRegion>
      <PageError failed={groupsFailed} what="결과" />
      <Pager page={groups} offset={gOffset} setOffset={setGOffset} />
      {r.bins && <p className="mw-muted">구간별 그룹 수: {r.bins.map((b) => `${b.label} ${b.groups}`).join(' · ')}</p>}

      <h3>제외 ({r.rows_excluded.toLocaleString('ko-KR')}행)</h3>
      <ul>
        {Object.entries(r.reason_counts).map(([k, n]) => (
          <li key={k}>
            {reasons[k] ?? k}: {n.toLocaleString('ko-KR')}행
          </li>
        ))}
        {r.excluded_targets.map((t) => (
          <li key={`${t.file_id}:${t.sheet_id}`}>
            자료 제외 — #{t.file_id} {t.file_name} {t.sheet ?? ''}: {t.reasons.join('; ')}
          </li>
        ))}
      </ul>
      {excluded && excluded.items.length > 0 && (
        <TableRegion label="제외 행 표">
          <table className="upload-table an-rows">
            <thead>
              <tr>
                <th scope="col">파일 · 시트 · 원본 행</th>
                <th scope="col">기간</th>
                <th scope="col">사유</th>
                <th scope="col">값</th>
              </tr>
            </thead>
            <tbody>
              {excluded.items.map((x, i) => (
                <tr key={`${id}-${excluded.offset + i}`}>
                  <td data-label="파일 · 시트 · 원본 행">
                    #{x.file_id} {x.file_name} · {x.sheet} · {x.row}행
                  </td>
                  <td data-label="기간">{x.period ?? '-'}</td>
                  <td data-label="사유">{reasons[x.reason] ?? x.reason}</td>
                  <td data-label="값">{x.value === null ? '(빈칸)' : String(x.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
      {excluded && excluded.all !== undefined && excluded.all > excluded.total && (
        <p className="mw-muted">
          목록은 처음 {excluded.total.toLocaleString('ko-KR')}행만 보관합니다 (사유별 행 수는 위).
        </p>
      )}
      <PageError failed={excludedFailed} what="제외" />
      <Pager page={excluded} offset={eOffset} setOffset={setEOffset} />

      <h3>근거 행 ({r.detail_total.toLocaleString('ko-KR')}행 — 계산에 들어간 원본 행)</h3>
      {rows && rows.items.length > 0 && (
        <TableRegion label="근거 행 표">
          <table className="upload-table an-rows">
            <thead>
              <tr>
                <th scope="col">파일 · 시트 · 원본 행</th>
                <th scope="col">기간</th>
                {r.group_labels.map((l) => (
                  <th key={l} scope="col">
                    {l}
                  </th>
                ))}
                <th scope="col">{c.field_label}</th>
              </tr>
            </thead>
            <tbody>
              {rows.items.map((x, i) => (
                <tr key={`${id}-${rows.offset + i}`}>
                  <td data-label="파일 · 시트 · 원본 행">
                    #{x.file_id} {x.file_name} · {x.sheet} · {x.row}행
                  </td>
                  <td data-label="기간">{x.period ?? '-'}</td>
                  {r.group_labels.map((l, i) => (
                    <td key={l} data-label={l}>
                      {x.keys[i] || '(전체)'}
                    </td>
                  ))}
                  <td data-label={c.field_label} className="an-num">
                    {fmt(x.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      )}
      {rows && rows.all !== undefined && rows.all > rows.total && (
        <p className="mw-muted">
          목록은 처음 {rows.total.toLocaleString('ko-KR')}행만 보관합니다 (계산은 전체 행으로 함).
        </p>
      )}
      <PageError failed={rowsFailed} what="근거 행" />
      <Pager page={rows} offset={rOffset} setOffset={setROffset} />

      <h3>사용한 자료</h3>
      <TableRegion label="사용한 자료 표">
        <table className="upload-table an-rows">
          <thead>
            <tr>
              <th scope="col">파일 · 시트 · 열</th>
              <th scope="col">단위 · 기간</th>
              <th scope="col">상태</th>
            </tr>
          </thead>
          <tbody>
            {detail.inputs.map((t) => (
              <tr key={`${t.file_id}:${t.sheet_id}`}>
                <td data-label="파일 · 시트 · 열">
                  #{t.file_id} {t.file_name} · {t.sheet ?? '-'} · {t.column ?? '-'}
                  <span className="mw-muted mw-block">sha256 {t.sha256.slice(0, 12)}…</span>
                </td>
                <td data-label="단위 · 기간">
                  {t.unit ?? '-'} {t.unit_source ? `(${t.unit_source})` : ''} ·{' '}
                  {t.period ?? (t.period_columns.length ? `행의 ${t.period_columns.join('·')} 열` : '-')}
                </td>
                <td data-label="상태">
                  {STATUS_LABEL[t.status]}
                  {t.reasons.map((x, i) => (
                    <span key={i} className="mw-muted mw-block">
                      {x}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableRegion>
    </section>
    </>
  )
}

export default Analysis
