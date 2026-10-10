import { useCallback, useEffect, useState } from 'react'
import {
  deleteAutomation,
  listAutomations,
  listFiles,
  runAutomation,
  runDownloadUrl,
  uploadFiles,
  type Automation,
  type AutomationRunResult,
  type StoredFile,
} from './api'
import { ProgressPanel } from './Progress'
import { REVIEW_STAGES } from './progressStages'
import ReviewPanel from './ReviewPanel'
import { formatDuration, formatTime, STATUS_CLASS } from './runFormat'

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

/** 저장된 자동화: 새 파일을 올려 저장된 규칙을 바로 적용한다 (새 열 등 확인할 항목만 검토). */
function Automations() {
  const [automations, setAutomations] = useState<Automation[] | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listAutomations()
      .then((list) => {
        setAutomations(list)
        setSelected((cur) => (cur !== null && list.some((a) => a.id === cur) ? cur : (list[0]?.id ?? null)))
      })
      .catch((err: unknown) => setError(`자동화 목록을 불러오지 못했습니다: ${errorText(err)}`))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const current = automations?.find((a) => a.id === selected) ?? null
  return (
    <section className="automation" aria-label="자동화">
      <h2>자동화</h2>
      <p className="upload-drop-hint">
        '취합 작업'에서 통합 결과를 만든 뒤 '자동화로 저장'하면 여기에 나타납니다. 새 파일을 올려 실행하면 저장된 매핑
        규칙·이름 연결·검증 조건·결과 열 순서를 그대로 적용하고, 저장된 규칙에 없는 새 열만 확인을 요청합니다.
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {automations !== null && automations.length === 0 && (
        <p className="upload-drop-hint">저장된 자동화가 없습니다.</p>
      )}
      {automations !== null && automations.length > 0 && (
        <ul className="automation-list">
          {automations.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                className={`automation-pick${a.id === selected ? ' is-selected' : ''}`}
                aria-pressed={a.id === selected}
                onClick={() => setSelected(a.id)}
              >
                <strong>{a.name}</strong>
                <span>
                  열 규칙 {a.column_rules.length} · 이름 연결 {Object.keys(a.entity_rules).length} · 실행 {a.run_count}회
                  {a.owner_name && ` · ${a.owner_name}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {current && (
        <AutomationDetail
          key={current.id}
          automation={current}
          onChanged={load}
          onDeleted={() => {
            setSelected(null)
            load()
          }}
        />
      )}
    </section>
  )
}

function AutomationDetail({
  automation,
  onChanged,
  onDeleted,
}: {
  automation: Automation
  onChanged: () => void
  onDeleted: () => void
}) {
  const [files, setFiles] = useState<StoredFile[]>([])
  const [targets, setTargets] = useState<number[]>([])
  const [baseline, setBaseline] = useState<number[]>([])
  const [period, setPeriod] = useState(automation.validation.expected_period ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AutomationRunResult | null>(null)
  const [openReview, setOpenReview] = useState<number | null>(null)
  const [running, setRunning] = useState(false) // 자동화 실행 중 (업로드 중과 구분)

  const loadFiles = useCallback(() => {
    listFiles()
      .then(setFiles)
      .catch(() => undefined)
  }, [])
  useEffect(() => {
    loadFiles()
  }, [loadFiles])

  async function upload(list: FileList | null) {
    if (!list || list.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const uploaded = await uploadFiles(Array.from(list))
      const ok = uploaded.filter((u) => u.id !== null && !u.error).map((u) => u.id as number)
      const failed = uploaded.filter((u) => u.id === null || u.error)
      if (failed.length) setError(`올리지 못한 파일: ${failed.map((f) => `${f.original_name} (${f.error})`).join(', ')}`)
      setTargets((prev) => [...new Set([...prev, ...ok])])
      setBaseline((prev) => prev.filter((id) => !ok.includes(id)))
      loadFiles()
    } catch (err) {
      setError(`업로드 실패: ${errorText(err)}`)
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    setRunning(true)
    setError(null)
    setResult(null)
    setOpenReview(null)
    try {
      const out = await runAutomation(automation.id, {
        file_ids: targets,
        baseline_file_ids: baseline,
        expected_period: period.trim() || null,
      })
      setResult(out)
      onChanged()
    } catch (err) {
      setError(`실행 실패: ${errorText(err)}`)
    } finally {
      setBusy(false)
      setRunning(false)
    }
  }

  function toggle(list: number[], id: number, on: boolean) {
    return on ? [...list, id] : list.filter((x) => x !== id)
  }

  const v = automation.validation
  return (
    <div className="automation-detail">
      <div className="automation-detail-head">
        <h3>{automation.name}</h3>
        <button
          type="button"
          className="mapping-confirm"
          disabled={busy}
          onClick={() => {
            if (!window.confirm(`자동화 '${automation.name}'을(를) 지울까요? 실행 기록과 결과 파일은 남습니다.`)) return
            deleteAutomation(automation.id)
              .then(onDeleted)
              .catch((err: unknown) => setError(`삭제 실패: ${errorText(err)}`))
          }}
        >
          자동화 삭제
        </button>
      </div>
      <p className="upload-drop-hint">
        저장: {formatTime(automation.updated_at)} (실행 #{automation.source_run_id ?? '-'}에서) · 필수 기관{' '}
        {v.required_entities?.length ? v.required_entities.join(', ') : '없음'} · 증감 기준{' '}
        {Math.round((v.change_threshold ?? 0.5) * 100)}%
        {automation.last_run &&
          ` · 마지막 실행 #${automation.last_run.id} ${automation.last_run.status_label} (${formatDuration(automation.last_run.duration_ms)})`}
      </p>

      <details className="automation-rules">
        <summary>
          저장된 규칙 보기 (열 {automation.column_rules.length}개, 이름 연결 {Object.keys(automation.entity_rules).length}
          개, 결과 열 {automation.output_format.columns?.length ?? 0}개)
        </summary>
        <ul>
          {automation.column_rules.map((r) => (
            <li key={r.key}>
              {r.column} → {r.field_label ?? '(해당 없음)'}
            </li>
          ))}
          {Object.entries(automation.entity_rules).map(([name, code]) => (
            <li key={`e-${name}`}>
              {name} → {code ?? '(그대로 둠)'}
            </li>
          ))}
        </ul>
      </details>

      <div className="automation-run">
        <label className="automation-upload">
          새 파일 올리기
          <input
            type="file"
            multiple
            accept=".xlsx,.xls,.csv"
            disabled={busy}
            onChange={(e) => {
              void upload(e.target.files)
              e.target.value = ''
            }}
          />
        </label>
        <fieldset className="automation-files">
          <legend>실행할 파일 (이번 달 자료)</legend>
          {files.length === 0 && <p className="upload-drop-hint">업로드한 파일이 없습니다.</p>}
          {files.map((f) => (
            <label key={f.id}>
              <input
                type="checkbox"
                checked={targets.includes(f.id)}
                disabled={baseline.includes(f.id)}
                onChange={(e) => setTargets((prev) => toggle(prev, f.id, e.target.checked))}
              />
              #{f.id} {f.original_name}
            </label>
          ))}
        </fieldset>
        <details className="automation-files">
          <summary>전월 자료 (선택, 전월 대비 이상치 검사)</summary>
          {files.map((f) => (
            <label key={f.id}>
              <input
                type="checkbox"
                checked={baseline.includes(f.id)}
                disabled={targets.includes(f.id)}
                onChange={(e) => setBaseline((prev) => toggle(prev, f.id, e.target.checked))}
              />
              #{f.id} {f.original_name}
            </label>
          ))}
        </details>
        <label className="automation-period">
          기준 기간 (예: 2026-10, 비우면 검사 안 함)
          <input type="text" value={period} placeholder="YYYY-MM" onChange={(e) => setPeriod(e.target.value)} />
        </label>
        <button
          type="button"
          className="mapping-run"
          disabled={busy || targets.length === 0}
          onClick={() => void run()}
        >
          {busy ? '실행 중…' : `자동화 실행 (${targets.length}개 파일)`}
        </button>
      </div>

      {running && <ProgressPanel stages={REVIEW_STAGES} title="자동화 규칙 적용 중" />}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="automation-result" role="status">
          <p className="integration-title">
            실행 #{result.run.id}:{' '}
            <span className={`runs-status ${STATUS_CLASS[result.run.status] ?? ''}`}>{result.run.status_label}</span>{' '}
            {result.summary}
          </p>
          <p className="review-counts">
            <span className="review-chip is-pending">
              확인 필요 {result.counts.pending_open}/{result.counts.pending}
            </span>
            <span className="review-chip is-auto">자동 적용 {result.counts.auto}</span>
            <span className="review-chip is-block">차단 {result.counts.block}</span>
            <span className="review-chip">경고 {result.counts.warning}</span>
          </p>
          {result.run.status === 'done' && (
            <p>
              결과 파일: <code>{result.run.output_path}</code> · <a href={runDownloadUrl(result.run.id)}>내려받기</a> ·
              소요 {formatDuration(result.run.duration_ms)}
            </p>
          )}
          {result.run.status === 'failed' && <p className="upload-error">{result.run.error}</p>}
          {result.run.status === 'review' && (
            <button type="button" className="mapping-confirm" onClick={() => setOpenReview(result.review_id)}>
              확인할 항목 검토하기
            </button>
          )}
        </div>
      )}
      {openReview !== null && (
        <ReviewPanel
          key={openReview}
          reviewId={openReview}
          onClose={() => {
            setOpenReview(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

export default Automations
