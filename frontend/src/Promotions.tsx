import { useCallback, useEffect, useState } from 'react'
import { decidePromotion, listPromotions, type PromotionCandidate } from './api'
import { formatTime } from './runFormat'

type Filter = 'pending' | 'approved' | 'rejected' | 'all'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'pending', label: '심사 대기' },
  { id: 'approved', label: '반영됨' },
  { id: 'rejected', label: '반려' },
  { id: 'all', label: '전체' },
]
const STATUS_LABEL: Record<PromotionCandidate['status'], string> = {
  pending: '심사 대기',
  approved: '반영됨',
  rejected: '반려',
}

/** 매핑 규칙 승격 심사: 서로 다른 사용자 N명 이상이 승인한 매핑 → 승인하면 전사 표준 사전에 반영 */
function Promotions() {
  const [filter, setFilter] = useState<Filter>('pending')
  const [data, setData] = useState<{ threshold: number; items: PromotionCandidate[] } | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback((f: Filter) => {
    listPromotions(f)
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  useEffect(() => {
    load(filter)
  }, [filter, load])

  async function decide(c: PromotionCandidate, decision: 'approve' | 'reject') {
    setBusy(c.id)
    setError(null)
    try {
      await decidePromotion(c.id, decision, notes[c.id]?.trim() || null)
      load(filter)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="admin" aria-label="승격 심사">
      <h2>매핑 규칙 승격 심사</h2>
      <p className="upload-drop-hint">
        같은 열 이름 → 표준 항목 매핑을 서로 다른 사용자 {data?.threshold ?? 3}명 이상이 승인하면 이 대기열에 올라옵니다.
        승인하면 전사 표준 사전(standard_terms)에 반영되어 모든 사용자의 열 매핑 1단계(정확 일치)에 자동 적용됩니다.
      </p>
      <div className="admin-filters" role="group" aria-label="상태">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`admin-filter${filter === f.id ? ' is-active' : ''}`}
            aria-pressed={filter === f.id}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {data && data.items.length === 0 && <p className="upload-drop-hint">해당하는 후보가 없습니다.</p>}
      {data && data.items.length > 0 && (
        <ul className="admin-list">
          {data.items.map((c) => (
            <li key={c.id} className="admin-item">
              <div className="admin-item-head">
                <strong>
                  {c.column_name} → {c.field_label ?? c.field_key}
                </strong>
                <span className={`admin-badge is-${c.status}`}>{STATUS_LABEL[c.status]}</span>
              </div>
              <p className="upload-drop-hint">
                승인한 사용자 {c.approver_count}명: {c.approvers.join(', ')} · 올라온 시각 {formatTime(c.created_at)}
                {c.reviewed_by_name && ` · 심사 ${c.reviewed_by_name} (${formatTime(c.reviewed_at)})`}
                {c.review_note && ` · 메모: ${c.review_note}`}
              </p>
              {c.status === 'pending' && (
                <div className="admin-actions">
                  <input
                    type="text"
                    aria-label={`${c.column_name} 심사 메모`}
                    placeholder="심사 메모 (선택)"
                    maxLength={500}
                    value={notes[c.id] ?? ''}
                    onChange={(e) => setNotes((prev) => ({ ...prev, [c.id]: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="review-approve"
                    disabled={busy !== null}
                    onClick={() => void decide(c, 'approve')}
                  >
                    승인 (표준 사전 반영)
                  </button>
                  <button
                    type="button"
                    className="review-reject"
                    disabled={busy !== null}
                    onClick={() => void decide(c, 'reject')}
                  >
                    반려
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default Promotions
