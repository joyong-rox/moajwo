import { useEffect, useState, type MouseEvent } from 'react'
import {
  completeReview,
  createReview,
  decideReviewItem,
  setRememberFormat,
  type Review,
  type ReviewCategory,
  type ReviewItem,
  type ValidationRequest,
} from './api'

const KIND_LABEL: Record<string, string> = {
  mapping: '열 매핑',
  entity: '기관·지역 이름',
  pii: '개인정보',
  number: '숫자 변환',
  ffill: '병합 셀 채움',
  header: '헤더 결합',
  sum_mismatch: '합계 불일치',
  required_entity_missing: '필수 기관 누락',
  period_mismatch: '기간 불일치',
  mom_outlier: '전월 대비 이상치',
  blank_value: '공백 값',
  unit_estimate: '단위 추정',
}

const GROUPS: { category: ReviewCategory; title: string; hint: string }[] = [
  { category: 'block', title: '차단 오류', hint: '원본 파일을 고친 뒤 다시 검사해야 통합할 수 있습니다.' },
  { category: 'pending', title: '확인 필요', hint: '승인하면 적용하고, 거부하면 적용하지 않습니다.' },
  { category: 'auto', title: '자동 적용', hint: '규칙에 따라 자동으로 적용되는 보정입니다.' },
]

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err)
}

function location(item: ReviewItem) {
  const parts = [item.file_name, item.sheet, item.row ? `${item.row}행` : null, item.column]
  return parts.filter(Boolean).join(' / ')
}

/** 통합 전 사전 검토 패널: 품질 점수 → 보정 예정 항목(이전 → 이후, 근거 펼치기) → 기억하기·검토 완료 */
function ReviewPanel({ request, onClose }: { request: ValidationRequest; onClose: () => void }) {
  const [review, setReview] = useState<Review | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyItem, setBusyItem] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    // 로딩 표시는 처음 값(true)과 '다시 검사' 처리기에서 켠다. 여기서는 결과만 반영한다.
    let cancelled = false
    createReview(request)
      .then((r) => !cancelled && setReview(r))
      .catch((err: unknown) => !cancelled && setError(`검토를 만들지 못했습니다: ${errorText(err)}`))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [request, version])

  function recheck() {
    setLoading(true)
    setError(null)
    setMessage(null)
    setVersion((v) => v + 1)
  }

  async function run(itemId: number | null, fn: () => Promise<Review>): Promise<boolean> {
    setBusyItem(itemId)
    setError(null)
    try {
      setReview(await fn())
      return true
    } catch (err) {
      setError(errorText(err))
      return false
    } finally {
      setBusyItem(null)
    }
  }

  return (
    <section className="review-panel" aria-label="통합 전 사전 검토">
      <div className="review-head">
        <h2>통합 전 사전 검토</h2>
        <button type="button" className="mapping-confirm" onClick={onClose}>
          닫기
        </button>
      </div>

      {loading && <p className="upload-drop-hint">검토 항목을 만드는 중… (LLM 판정이 필요한 열이 있으면 수십 초)</p>}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {review && !loading && (
        <>
          <ScoreHeader review={review} />

          {GROUPS.map((g) => (
            <ItemGroup
              key={g.category}
              review={review}
              category={g.category}
              title={g.title}
              hint={g.hint}
              busyItem={busyItem}
              onDecide={(item, decision, choice) =>
                void run(item.id, () => decideReviewItem(review.id, item.id, decision, choice))
              }
            />
          ))}

          {review.counts.warning > 0 && (
            <details className="review-warnings">
              <summary>검증 경고 {review.counts.warning}건 (통합은 진행, 참고용)</summary>
              <ul className="review-items">
                {review.items
                  .filter((i) => i.category === 'warning')
                  .map((i) => (
                    <ItemRow key={i.id} item={i} disabled />
                  ))}
              </ul>
            </details>
          )}

          <div className="review-footer">
            <label className="review-remember">
              <input
                type="checkbox"
                checked={review.remember_format}
                disabled={review.status !== 'in_review' || busyItem !== null}
                onChange={(e) => {
                  const previous = review
                  // 바로 체크 표시를 바꾸고 저장한다. 저장에 실패하면 원래대로 되돌린다.
                  setReview({ ...review, remember_format: e.target.checked })
                  void run(null, () => setRememberFormat(review.id, e.target.checked)).then((saved) => {
                    if (!saved) setReview(previous)
                  })
                }}
              />
              다음부터 이 형식 기억하기
            </label>
            <p className="upload-drop-hint">
              선택하면 검토 완료 시 승인한 열 이름과 기관·지역 이름을 학습해 다음 파일부터 자동으로 적용합니다. 선택하지
              않으면 이번 검토에만 적용합니다.
            </p>
            <div className="review-actions">
              <button type="button" className="mapping-confirm" disabled={busyItem !== null} onClick={recheck}>
                다시 검사
              </button>
              <button
                type="button"
                className="mapping-run"
                disabled={!review.can_complete || busyItem !== null}
                onClick={() =>
                  void run(null, async () => {
                    const done = await completeReview(review.id)
                    const learned = done.learned
                    setMessage(
                      learned && (learned.columns || learned.names)
                        ? `검토 완료 — 열 이름 ${learned.columns}개, 기관·지역 이름 ${learned.names}개를 기억했습니다.`
                        : '검토 완료 — 통합 준비가 끝났습니다.',
                    )
                    return done
                  })
                }
              >
                {review.status === 'ready' ? '검토 완료됨' : '검토 완료'}
              </button>
            </div>
            {message && (
              <p className="mapping-message" role="status">
                {message}
              </p>
            )}
          </div>
        </>
      )}
    </section>
  )
}

function ScoreHeader({ review }: { review: Review }) {
  const level = review.counts.block ? 'is-block' : review.counts.pending_open ? 'is-pending' : 'is-ok'
  return (
    <div className={`review-score ${level}`}>
      <div className="review-score-value" aria-label={`데이터 품질 점수 ${review.score}점`}>
        <strong>{review.score}</strong>
        <span>/ 100</span>
      </div>
      <div className="review-score-text">
        <p className="review-summary">{review.summary}</p>
        <p className="review-counts">
          <span className="review-chip is-block">차단 {review.counts.block}</span>
          <span className="review-chip is-pending">
            확인 필요 {review.counts.pending_open}/{review.counts.pending}
          </span>
          <span className="review-chip is-auto">자동 적용 {review.counts.auto}</span>
          <span className="review-chip">경고 {review.counts.warning}</span>
        </p>
        <p className="upload-drop-hint">
          점수: 100 − 차단 25 × 건수 − 남은 확인 필요 5 × 건수 − 경고 2 × 건수(최대 10). 차단이 있으면 최고 59점.
          {review.files.some((f) => f.excluded) &&
            ` · 이번 통합에서 제외: ${review.files
              .filter((f) => f.excluded)
              .map((f) => f.name)
              .join(', ')}`}
        </p>
      </div>
    </div>
  )
}

type GroupProps = {
  review: Review
  category: ReviewCategory
  title: string
  hint: string
  busyItem: number | null
  onDecide: (item: ReviewItem, decision: 'approved' | 'rejected', choice: string | null) => void
}

function ItemGroup({ review, category, title, hint, busyItem, onDecide }: GroupProps) {
  const items = review.items.filter((i) => i.category === category)
  return (
    <section className={`review-group is-${category}`}>
      <h3>
        {title} {items.length}건 <span className="review-group-hint">{hint}</span>
      </h3>
      {items.length === 0 ? (
        <p className="upload-drop-hint">없음</p>
      ) : (
        <ul className="review-items">
          {items.map((i) => (
            <ItemRow
              key={i.id}
              item={i}
              disabled={review.status !== 'in_review' || busyItem !== null}
              busy={busyItem === i.id}
              onDecide={onDecide}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

type Choice = { value: string; label: string }

function choicesOf(item: ReviewItem): Choice[] {
  const candidates = item.payload.candidates ?? []
  if (item.kind === 'mapping') return candidates.map((c) => ({ value: c.key ?? '', label: `${c.label} (${(c.score ?? 0).toFixed(2)})` }))
  if (item.kind === 'entity') return candidates.map((c) => ({ value: c.code ?? '', label: `${c.name} (${c.code})` }))
  return []
}

type RowProps = {
  item: ReviewItem
  disabled?: boolean
  busy?: boolean
  onDecide?: GroupProps['onDecide']
}

function ItemRow({ item, disabled, busy, onDecide }: RowProps) {
  const choices = choicesOf(item)
  const initial = item.choice ?? (item.kind === 'mapping' ? item.payload.field_key ?? choices[0]?.value : choices[0]?.value) ?? ''
  const [choice, setChoice] = useState(initial)
  const stop = (e: MouseEvent) => {
    // 요약 줄 안의 버튼·선택 상자를 눌러도 펼치기/접기가 되지 않게 한다
    e.preventDefault()
    e.stopPropagation()
  }
  const decided = item.decision ? (item.decision === 'approved' ? '승인됨' : '거부됨') : null
  return (
    <li>
      <details className={`review-item is-${item.category}${item.decision ? ` is-${item.decision}` : ''}${item.excluded ? ' is-excluded' : ''}`}>
        <summary>
          <span className="review-kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
          <span className="review-change">
            <span className="review-before">{item.before ?? '-'}</span>
            <span className="review-arrow" aria-hidden="true">
              →
            </span>
            <span className="review-after">{item.after ?? '-'}</span>
          </span>
          <span className="review-where">{location(item)}</span>
          {item.category === 'pending' && onDecide && (
            <span className="review-controls" onClick={stop}>
              {choices.length > 0 && (
                <select
                  aria-label={`${item.before} 적용할 대상`}
                  value={choice}
                  disabled={disabled}
                  onChange={(e) => setChoice(e.target.value)}
                >
                  {choices.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className="review-approve"
                disabled={disabled || (choices.length === 0 && item.kind !== 'pii')}
                aria-pressed={item.decision === 'approved'}
                onClick={() => onDecide(item, 'approved', choices.length ? choice : null)}
              >
                승인
              </button>
              <button
                type="button"
                className="review-reject"
                disabled={disabled}
                aria-pressed={item.decision === 'rejected'}
                onClick={() => onDecide(item, 'rejected', null)}
              >
                거부
              </button>
              {busy ? <span className="review-state">저장 중…</span> : decided && <span className="review-state">{decided}</span>}
            </span>
          )}
          {item.category !== 'pending' && decided && <span className="review-state">{decided}</span>}
        </summary>
        <p className="review-reason">
          <span className={`review-source is-${item.reason_source}`}>
            {item.reason_source === 'llm' ? 'LLM 생성 문장' : '규칙 문장'}
          </span>{' '}
          {item.reason}
        </p>
      </details>
    </li>
  )
}

export default ReviewPanel
