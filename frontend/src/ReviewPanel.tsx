import { useEffect, useRef, useState, type MouseEvent } from 'react'
import {
  completeReview,
  createReview,
  decideReviewItem,
  fetchFields,
  getReview,
  listFiles,
  recheckReview,
  setRememberFormat,
  type Review,
  type ReviewChangeItem,
  type StoredFile,
  type ReviewCategory,
  type ReviewItem,
  type StandardFieldInfo,
  type ValidationRequest,
} from './api'
import IntegrationResult from './IntegrationResult'
import { ProgressPanel } from './Progress'
import { REVIEW_STAGES } from './progressStages'

const KIND_LABEL: Record<string, string> = {
  mapping: '열 매핑',
  entity: '기관·지역 이름',
  pii: '개인정보',
  number: '숫자 변환',
  ffill: '병합 셀 채움',
  trim: '공백 정리',
  header: '헤더 결합',
  sum_mismatch: '합계 불일치',
  required_entity_missing: '필수 기관 누락',
  period_mismatch: '기간 불일치',
  mom_outlier: '전월 대비 이상치',
  blank_value: '공백 값',
  unit_estimate: '단위 추정',
  header_missing: '헤더 확인 필요',
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
function ReviewPanel({
  request,
  reviewId,
  onClose,
}: {
  request?: ValidationRequest
  reviewId?: number // 이미 만든 검토(자동화 실행에서 확인할 항목이 남은 경우)를 이어서 연다
  onClose: () => void
}) {
  const [review, setReview] = useState<Review | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyItem, setBusyItem] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [uploaded, setUploaded] = useState<StoredFile[]>([])
  // 다시 검사할 때 바꿀 파일: {검토 중인 파일 id: 고친 파일 id}
  const [replace, setReplace] = useState<Record<number, number>>({})
  // 긴 근거 문장을 크게 보여주는 창
  const [reasonItem, setReasonItem] = useState<ReviewItem | null>(null)
  // 열 매핑을 후보(유사도 상위) 밖의 표준 항목으로도 고를 수 있게 전체 표준 항목을 불러 둔다
  const [fields, setFields] = useState<StandardFieldInfo[]>([])
  useEffect(() => {
    fetchFields()
      .then(setFields)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    // 패널이 열릴 때 한 번 검토를 만든다 (새로 열면 App이 패널을 새로 만든다)
    let cancelled = false
    const load = reviewId !== undefined ? getReview(reviewId) : request ? createReview(request) : null
    if (load === null) return
    load
      .then((r) => !cancelled && setReview(r))
      .catch((err: unknown) => !cancelled && setError(`검토를 만들지 못했습니다: ${errorText(err)}`))
      .finally(() => !cancelled && setLoading(false))
    listFiles()
      .then((list) => !cancelled && setUploaded(list))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [request, reviewId])

  async function recheck(current: Review) {
    // 이전 검토의 승인/거부·제외·기억하기 선택을 이어받아 새 검사 세션을 만든다
    setLoading(true)
    setMessage(null)
    const changed = Object.fromEntries(Object.entries(replace).filter(([from, to]) => Number(from) !== to))
    const ok = await run(null, () => recheckReview(current.id, changed))
    if (ok) setReplace({})
    setLoading(false)
    listFiles()
      .then(setUploaded)
      .catch(() => undefined)
  }

  // 결정이 거부된 뒤(권한이 바뀐 경우 등) 서버의 최신 상태로 화면을 맞춘다
  function refresh(): Promise<void> {
    if (!review) return Promise.resolve()
    return getReview(review.id).then(setReview, () => undefined)
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
    <section className="review-panel review-panel-wide" aria-label="통합 전 사전 검토">
      <div className="review-head">
        <h2>통합 전 사전 검토</h2>
        <button type="button" className="mapping-confirm" onClick={onClose}>
          닫기
        </button>
      </div>

      {loading && <ProgressPanel stages={REVIEW_STAGES} title="검토 항목을 만드는 중 (LLM 판정이 필요한 열이 있으면 수십 초)" />}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {review && !loading && (
        <>
          <ScoreHeader review={review} />
          {review.items.some((i) => i.kind === 'header_missing') && (
            <div className="review-header-warning" role="alert">
              <strong>머리글(열 이름)을 확인하세요</strong>
              <ul>
                {review.items
                  .filter((i) => i.kind === 'header_missing')
                  .map((i) => (
                    <li key={i.id}>
                      {i.file_name}: {i.reason}
                    </li>
                  ))}
              </ul>
            </div>
          )}
          <RecheckHistory review={review} />

          {GROUPS.map((g) => (
            <ItemGroup
              key={g.category}
              review={review}
              category={g.category}
              title={g.title}
              hint={g.hint}
              busyItem={busyItem}
              onDecide={(item, decision, choice) =>
                void run(item.id, () => decideReviewItem(review.id, item.id, decision, choice)).then((ok) => {
                  if (!ok) void refresh()
                })
              }
              onOpenReason={setReasonItem}
              fields={fields}
            />
          ))}

          {review.counts.warning > 0 && (
            <details className="review-warnings">
              <summary>검증 경고 {review.counts.warning}건 (통합은 진행, 참고용)</summary>
              <ul className="review-items">
                {review.items
                  .filter((i) => i.category === 'warning')
                  .map((i) => (
                    <ItemRow key={i.id} item={i} disabled onOpenReason={setReasonItem} />
                  ))}
              </ul>
            </details>
          )}

          <div className="review-footer">
            <label className="review-remember">
              <input
                type="checkbox"
                checked={review.remember_format}
                disabled={review.status !== 'in_review' || busyItem !== null || !review.can_edit}
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
              <button
                type="button"
                className="mapping-confirm"
                disabled={busyItem !== null || review.status === 'superseded' || !review.can_edit}
                onClick={() => void recheck(review)}
              >
                다시 검사 (결정 이어받기)
              </button>
              <button
                type="button"
                className="mapping-run"
                disabled={!review.can_complete || busyItem !== null || !review.can_edit}
                onClick={() =>
                  void run(null, async () => {
                    const done = await completeReview(review.id)
                    const learned = done.learned
                    const skipped = learned?.names_skipped
                      ? ` 기관·지역 이름 ${learned.names_skipped}개는 공용 용어사전이라 관리자만 등록할 수 있어 기억하지 않았습니다.`
                      : ''
                    setMessage(
                      (learned && (learned.columns || learned.names)
                        ? `검토 완료 — 열 이름 ${learned.columns}개, 기관·지역 이름 ${learned.names}개를 기억했습니다.`
                        : '검토 완료 — 통합 준비가 끝났습니다.') + skipped,
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
            {review.status === 'ready' && review.can_edit && (
              <IntegrationResult
                reviewId={review.id}
                defaultIncludeUnmapped={review.include_unmapped_default}
                piiIncluded={review.items.some(
                  (i) => i.kind === 'pii' && !review.files.some((f) => f.id === i.file_id && f.excluded),
                )}
              />
            )}
            <details className="review-replace">
              <summary>원본을 고친 파일로 바꿔 다시 검사</summary>
              <p className="upload-drop-hint">
                고친 파일을 먼저 업로드한 뒤 아래에서 고르세요. 같은 형식이므로 열 매핑 확정은 이어받고, 개인정보 확인은
                새로 받습니다.
              </p>
              {review.files.map((f) => (
                <label key={f.id} className="review-replace-row">
                  <span>{f.name}</span>
                  <select
                    aria-label={`${f.name} 대신 검사할 파일`}
                    value={replace[f.id] ?? f.id}
                    onChange={(e) => setReplace((prev) => ({ ...prev, [f.id]: Number(e.target.value) }))}
                  >
                    <option value={f.id}>그대로 (#{f.id})</option>
                    {uploaded
                      .filter((u) => u.id !== f.id && !review.files.some((rf) => rf.id === u.id))
                      .map((u) => (
                        <option key={u.id} value={u.id}>
                          #{u.id} {u.original_name}
                        </option>
                      ))}
                  </select>
                </label>
              ))}
            </details>
          </div>
        </>
      )}
      {reasonItem && <ReasonDialog item={reasonItem} onClose={() => setReasonItem(null)} />}
    </section>
  )
}

function ScoreHeader({ review }: { review: Review }) {
  // 남이 올린 파일의 매핑·개인정보 항목 (서버가 판단한 조회 전용 항목)
  const readOnlyFiles = [
    ...new Set(
      review.items.filter((i) => i.category === 'pending' && i.can_change === false).map((i) => i.file_name ?? `#${i.file_id}`),
    ),
  ]
  const level = review.counts.block ? 'is-block' : review.counts.pending_open ? 'is-pending' : 'is-ok'
  return (
    <div className={`review-score ${level}`}>
      <div className="review-score-value" aria-label={`데이터 품질 점수 ${review.score}점`}>
        <strong>{review.score}</strong>
        <span>/ 100</span>
      </div>
      <div className="review-score-text">
        {!review.can_edit && (
          <p className="review-automation">
            {review.created_by_name ?? '다른 사용자'}님의 검토입니다 — 조회만 할 수 있습니다 (승인·완료·통합은 만든 사람만).
          </p>
        )}
        {review.can_edit && readOnlyFiles.length > 0 && (
          <p className="review-automation" role="note">
            조회 전용이며 파일을 올린 사용자만 변경할 수 있습니다 — {readOnlyFiles.join(', ')}의 열 매핑·개인정보 항목은 승인·거부할 수 없습니다.
          </p>
        )}
        {review.automation_name && (
          <p className="review-automation">
            자동화 '{review.automation_name}'의 규칙을 적용한 검토입니다 — 저장된 규칙에 없는 새 열 등만 확인하면 됩니다.
          </p>
        )}
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

function changeLabel(c: ReviewChangeItem) {
  return `${KIND_LABEL[c.kind] ?? c.kind}: ${c.before ?? '-'} → ${c.after ?? '-'}`
}

/** 검사 이력(#1 → #2 …)과 이전 검사 대비 변화 */
function RecheckHistory({ review }: { review: Review }) {
  if (review.history.length < 2 || !review.changes) return null
  const c = review.changes
  const delta = (pair: [number, number], unit = '') => `${pair[0]}${unit} → ${pair[1]}${unit}`
  return (
    <div className="review-history">
      <p className="review-history-chain">
        검사 이력:{' '}
        {review.history.map((h, i) => (
          <span key={h.id} className={`review-chip${h.id === review.id ? ' is-current' : ''}`}>
            {i + 1}차 #{h.id} · {h.score}점
          </span>
        ))}
      </p>
      <details className="review-changes" open>
        <summary>
          이전 검사 대비: 점수 {delta(c.score, '점')} · 차단 {delta(c.blocks)} · 남은 확인 필요 {delta(c.pending_open)} · 해결{' '}
          {c.resolved.length}건 · 새로 생김 {c.added.length}건
          {Object.keys(c.replaced_files).length > 0 && ` · 교체한 파일 ${Object.keys(c.replaced_files).length}개`}
        </summary>
        {c.resolved.length > 0 && (
          <ul className="review-change-list is-resolved">
            {c.resolved.map((r, i) => (
              <li key={`r${i}`}>해결 — {changeLabel(r)}</li>
            ))}
          </ul>
        )}
        {c.added.length > 0 && (
          <ul className="review-change-list is-added">
            {c.added.map((r, i) => (
              <li key={`a${i}`}>새로 생김 — {changeLabel(r)}</li>
            ))}
          </ul>
        )}
      </details>
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
  onOpenReason: (item: ReviewItem) => void
  fields: StandardFieldInfo[]
}

function ItemGroup({ review, category, title, hint, busyItem, onDecide, onOpenReason, fields }: GroupProps) {
  const items = review.items.filter((i) => i.category === category)
  return (
    <section className={`review-group is-${category}`}>
      <h3>
        {title} {items.length}건{' '}
        {/* 차단이 없으면 '고쳐야 통합할 수 있다'는 안내를 보이지 않는다 */}
        {(category !== 'block' || items.length > 0) && <span className="review-group-hint">{hint}</span>}
      </h3>
      {items.length === 0 ? (
        <p className="upload-drop-hint">없음</p>
      ) : (
        <>
          <div className="review-cols" aria-hidden="true">
            <span>종류</span>
            <span>이전 → 이후</span>
            <span>위치</span>
            <span>{category === 'pending' ? '결정' : '상태'}</span>
          </div>
          <ul className="review-items">
            {items.map((i) => (
              <ItemRow
                key={i.id}
                item={i}
                disabled={review.status !== 'in_review' || busyItem !== null || !review.can_edit || i.can_change === false}
                busy={busyItem === i.id}
                onDecide={onDecide}
                onOpenReason={onOpenReason}
                fields={fields}
              />
            ))}
          </ul>
        </>
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

// 이보다 긴 근거(주로 LLM 문장)는 두 줄로 줄이고 '전체 보기' 창으로 연다
const LONG_REASON = 110

function shortReason(text: string) {
  return text.length > LONG_REASON ? `${text.slice(0, LONG_REASON)}…` : text
}

type RowProps = {
  item: ReviewItem
  disabled?: boolean
  busy?: boolean
  onDecide?: GroupProps['onDecide']
  onOpenReason?: (item: ReviewItem) => void
  fields?: StandardFieldInfo[]
}

function ItemRow({ item, disabled, busy, onDecide, onOpenReason, fields = [] }: RowProps) {
  const choices = choicesOf(item)
  // 후보에 없는 표준 항목 (열 매핑만)
  const others =
    item.kind === 'mapping' ? fields.filter((f) => !choices.some((c) => c.value === f.key)).map((f) => ({ value: f.key, label: f.label })) : []
  const initial = item.choice ?? (item.kind === 'mapping' ? item.payload.field_key ?? choices[0]?.value : choices[0]?.value) ?? ''
  const [choice, setChoice] = useState(initial)
  const stop = (e: MouseEvent) => {
    // 요약 줄 안의 버튼·선택 상자를 눌러도 펼치기/접기가 되지 않게 한다
    e.preventDefault()
    e.stopPropagation()
  }
  const decided = item.decision ? (item.decision === 'approved' ? '승인됨' : '거부됨') : null
  const where = location(item)
  const long = item.reason.length > LONG_REASON || item.reason_source === 'llm'
  return (
    <li>
      <details className={`review-item review-compact is-${item.category}${item.decision ? ` is-${item.decision}` : ''}${item.excluded ? ' is-excluded' : ''}`}>
        {/* 접힌 상태에서도 마우스를 올리면 근거 요약이 툴팁으로 보인다 */}
        <summary title={`근거: ${shortReason(item.reason)}`}>
          <span className="review-kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
          <span className="review-change">
            <span className="review-before">{item.before ?? '-'}</span>
            <span className="review-arrow" aria-hidden="true">
              →
            </span>
            <span className="review-after">{item.after ?? '-'}</span>
          </span>
          <span className="review-where" title={where}>
            {where}
          </span>
          {item.category === 'pending' && onDecide ? (
            <span className="review-controls" onClick={stop}>
              {(choices.length > 0 || others.length > 0) && (
                <select
                  aria-label={`${item.before} 적용할 대상`}
                  value={choice}
                  disabled={disabled}
                  onChange={(e) => setChoice(e.target.value)}
                >
                  {choice === '' && <option value="">표준 항목 선택</option>}
                  {others.length > 0 ? (
                    <>
                      {choices.length > 0 && (
                      <optgroup label="추천 후보">
                        {choices.map((c) => (
                          <option key={c.value} value={c.value}>
                            {c.label}
                          </option>
                        ))}
                      </optgroup>
                      )}
                      <optgroup label="다른 표준 항목">
                        {others.map((c) => (
                          <option key={c.value} value={c.value}>
                            {c.label}
                          </option>
                        ))}
                      </optgroup>
                    </>
                  ) : (
                    choices.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))
                  )}
                </select>
              )}
              <button
                type="button"
                className="review-approve"
                disabled={disabled || (item.kind !== 'pii' && (choices.length === 0 && others.length === 0 || choice === ''))}
                aria-pressed={item.decision === 'approved'}
                onClick={() => onDecide(item, 'approved', choices.length || others.length ? choice : null)}
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
              {busy ? (
                <span className="review-state">저장 중…</span>
              ) : item.can_change === false ? (
                <span className="review-state">조회 전용</span>
              ) : (
                decided && <span className="review-state">{decided}</span>
              )}
            </span>
          ) : (
            <span className="review-controls">{decided && <span className="review-state">{decided}</span>}</span>
          )}
        </summary>
        <div className="review-reason">
          <span className={`review-source is-${item.reason_source}`}>
            {item.reason_source === 'llm' ? 'LLM 생성 문장' : '규칙 문장'}
          </span>{' '}
          <span className={`review-reason-text${long ? ' is-clamped' : ''}`}>{item.reason}</span>
          {long && onOpenReason && (
            <button type="button" className="review-reason-more" onClick={() => onOpenReason(item)}>
              전체 보기
            </button>
          )}
        </div>
      </details>
    </li>
  )
}

/** 상세 보정 근거 전체를 보여주는 창 (Esc·바깥 클릭·닫기 버튼으로 닫힘) */
function ReasonDialog({ item, onClose }: { item: ReviewItem; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
  }, [])
  return (
    <dialog
      ref={ref}
      className="review-dialog"
      aria-labelledby="review-dialog-title"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close() // 바깥(배경) 클릭
      }}
    >
      <div className="review-dialog-body">
        <p className="review-dialog-kind">{KIND_LABEL[item.kind] ?? item.kind}</p>
        <h3 id="review-dialog-title" className="review-change">
          <span className="review-before">{item.before ?? '-'}</span>
          <span className="review-arrow" aria-hidden="true">
            →
          </span>
          <span className="review-after">{item.after ?? '-'}</span>
        </h3>
        <p className="review-dialog-where">{location(item)}</p>
        <p className="review-dialog-reason">
          <span className={`review-source is-${item.reason_source}`}>
            {item.reason_source === 'llm' ? 'LLM 생성 문장' : '규칙 문장'}
          </span>{' '}
          {item.reason}
        </p>
        <div className="review-actions">
          <button type="button" className="mapping-confirm" onClick={() => ref.current?.close()} autoFocus>
            닫기
          </button>
        </div>
      </div>
    </dialog>
  )
}

export default ReviewPanel
