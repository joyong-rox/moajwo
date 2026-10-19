import { useEffect, useState } from 'react'
import { applyBatch, BatchConflictError, previewBatch, type BatchPlan, type BatchTarget, type PlanItem } from './api'
import { STATUS_TEXT } from './mappingText'

const ACTION_TEXT: Record<PlanItem['action'], string> = {
  apply: '새로 확정',
  overwrite: '기존 확정 변경',
  unchanged: '이미 같음',
  excluded: '제외',
}

const keyOf = (i: { file_id: number; sheet_id: string; column_id: string }) =>
  `${i.file_id}|${i.sheet_id}|${i.column_id}`

const statusText = (s: PlanItem['current_status']) => (s === 'unknown' ? '-' : STATUS_TEXT[s])

/** 일괄 확정 확인: 대상별 상태·제안 근거·기존·변경 매핑과 적용 여부를 먼저 보여 주고,
 * 사용자가 고른 대상만 확인한 상태 그대로일 때 한 번에 적용한다. */
function BatchConfirm({
  targets,
  title,
  note,
  onDone,
  onCancel,
}: {
  targets: BatchTarget[]
  title: string
  note?: string
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [plan, setPlan] = useState<BatchPlan | null>(null)
  // 행별 포함 여부: 새로 확정은 기본 포함(개별 제외 가능), 기존 확정 변경은 기본 제외(명시적으로 골라야 함)
  const [include, setInclude] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [problems, setProblems] = useState<string[]>([])
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let alive = true
    previewBatch(targets)
      .then((p) => {
        if (!alive) return
        setPlan(p)
        setInclude(Object.fromEntries(p.items.filter((i) => i.action === 'apply').map((i) => [keyOf(i), true])))
      })
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      alive = false
    }
  }, [targets, version])

  const chosen = plan
    ? plan.items.filter((i) => (i.action === 'apply' || i.action === 'overwrite') && include[keyOf(i)])
    : []

  async function apply() {
    if (!plan || busy || chosen.length === 0) return
    setBusy(true)
    setError(null)
    setProblems([])
    try {
      const items = chosen.map((i) => ({
        file_id: i.file_id,
        sheet_id: i.sheet_id,
        column_id: i.column_id,
        field_key: i.new_field_key,
        expected_confirmed: i.current_confirmed,
        expected_field_key: i.current_field_key,
        overwrite: i.action === 'overwrite',
      }))
      const res = await applyBatch(items)
      onDone(
        `${res.applied}개 열을 확정했습니다 (파일 ${res.files}개${res.unchanged ? `, 이미 같음 ${res.unchanged}개` : ''}).`,
      )
    } catch (err) {
      if (err instanceof BatchConflictError) {
        setError(`${err.message} 아래 사유를 확인한 뒤 다시 확인하세요.`)
        setProblems(err.problems)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mw-confirm" aria-label="일괄 확정 확인" aria-live="polite">
      <h3>{title}</h3>
      {note && <p className="mw-muted">{note}</p>}
      {!plan && !error && <p className="mw-muted">대상을 확인하는 중… (저장하지 않음)</p>}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {problems.length > 0 && (
        <ul className="mw-problems">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {plan && (
        <>
          <p className="mw-confirm-summary">
            선택 {plan.selected}개 · 새로 확정 {plan.applicable}개 · 기존 확정 변경 후보 {plan.overwrite}개 · 이미 같음{' '}
            {plan.unchanged}개 · 제외 {plan.excluded}개 → <strong>실제 적용 {chosen.length}개</strong>
          </p>
          <div className="upload-table-wrap">
            <table className="upload-table mw-confirm-table">
              <thead>
                <tr>
                  <th scope="col">적용</th>
                  <th scope="col">파일 · 시트 · 열</th>
                  <th scope="col">상태 · 제안 근거</th>
                  <th scope="col">기존 매핑</th>
                  <th scope="col">바꿀 매핑</th>
                </tr>
              </thead>
              <tbody>
                {plan.items.map((i) => {
                  const key = keyOf(i)
                  const selectable = i.action === 'apply' || i.action === 'overwrite'
                  return (
                    <tr key={key} className={`mw-plan-${i.action}`}>
                      <td data-label="적용">
                        <div className="mw-cell">
                          {selectable ? (
                            <label className="mw-overwrite">
                              <input
                                type="checkbox"
                                checked={include[key] ?? false}
                                onChange={(e) => setInclude((prev) => ({ ...prev, [key]: e.target.checked }))}
                              />
                              {i.action === 'overwrite' ? '기존 확정을 바꿈' : '포함'}
                            </label>
                          ) : (
                            <span className={`upload-tag mw-action-${i.action}`}>{ACTION_TEXT[i.action]}</span>
                          )}
                          {i.reason && <span className="mw-muted mw-block">{i.reason}</span>}
                        </div>
                      </td>
                      <td data-label="파일 · 시트 · 열">
                        <div className="mw-cell">
                          <span className="mw-block">{i.file_name}</span>
                          <span className="mw-muted mw-block">
                            {i.sheet} · <strong>{i.column}</strong>
                          </span>
                        </div>
                      </td>
                      <td data-label="상태 · 제안 근거">
                        <div className="mw-cell">
                          {i.current_status !== 'unknown' && (
                            <span className={`upload-tag mapping-${i.current_status}`}>
                              {statusText(i.current_status)}
                            </span>
                          )}
                          {i.suggestion_reason && <span className="mw-muted mw-block">{i.suggestion_reason}</span>}
                        </div>
                      </td>
                      <td data-label="기존 매핑">
                        <div className="mw-cell">
                          {i.current_confirmed ? `${i.current_label ?? '해당 없음'} (사용자 확정)` : '미확정'}
                        </div>
                      </td>
                      <td data-label="바꿀 매핑">
                        <div className="mw-cell">{i.new_label ?? '해당 없음'}</div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="admin-actions">
        <button
          type="button"
          className="mapping-run"
          disabled={!plan || busy || chosen.length === 0}
          onClick={() => void apply()}
        >
          {busy ? '적용 중…' : `${chosen.length}개 확정 적용`}
        </button>
        {problems.length > 0 && (
          <button
            type="button"
            className="mapping-confirm"
            disabled={busy}
            onClick={() => {
              setError(null)
              setProblems([])
              setPlan(null)
              setVersion((v) => v + 1)
            }}
          >
            현재 상태로 다시 확인
          </button>
        )}
        <button type="button" className="mapping-confirm" disabled={busy} onClick={onCancel}>
          취소
        </button>
      </div>
      {plan && !busy && chosen.length === 0 && (
        <p className="wk-why" role="note">
          <strong>누를 수 없음</strong> 적용할 항목이 없습니다 — 표에서 적용할 열을 고르세요.
        </p>
      )}
    </section>
  )
}

export default BatchConfirm
