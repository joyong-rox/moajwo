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
import { Banner, NavLink, StatusCards, WorkflowSteps, type Step } from './Workflow'

const RECHECK_LABEL = '다시 검사 — 결정 이어받기'
// 검토 전체에 대한 요청(완료·다시 검사·형식 기억하기)이 진행 중 — 항목 번호와 겹치지 않는 값.
// 요청이 끝날 때까지 버튼을 막아, 빠르게 두 번 눌러도 요청이 두 번 가지 않게 한다.
const PANEL_BUSY = -1

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
  quantity_not_number: '수량으로 계산하지 못한 값',
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
  onLoaded,
}: {
  request?: ValidationRequest
  reviewId?: number // 이미 만든 검토(수동·자동화)를 이어서 연다 — 주소 /reviews?review=번호
  onClose: () => void
  onLoaded?: (review: Review) => void // 지금 보이는 검토 (주소·단계 안내를 맞춘다)
}) {
  const [review, setReview] = useState<Review | null>(null)
  // 마지막으로 보낸 요청 번호 — 늦게 도착한 이전 응답이 지금 상태(다시 검사한 새 검토 등)를 덮어쓰지 않게 한다
  const seq = useRef(0)
  // 이 검토로 만든 결과(실행) — 단계 안내의 '결과'를 서버 기록으로 정한다
  const [runsDone, setRunsDone] = useState(0)
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
    const my = ++seq.current
    load
      .then((r) => !cancelled && my === seq.current && setReview(r))
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
    const ok = await run(PANEL_BUSY, () => recheckReview(current.id, changed))
    if (ok) setReplace({})
    setLoading(false)
    listFiles()
      .then(setUploaded)
      .catch(() => undefined)
  }

  useEffect(() => {
    if (review) onLoaded?.(review)
  }, [review, onLoaded])

  // 결정·통합이 거부된 뒤(권한·확정이 바뀐 경우 등) 서버의 최신 상태로 화면을 맞춘다
  function refresh(): Promise<void> {
    if (!review) return Promise.resolve()
    const my = ++seq.current
    return getReview(review.id).then(
      (r) => {
        if (my === seq.current) setReview(r)
      },
      () => undefined,
    )
  }

  async function run(itemId: number | null, fn: () => Promise<Review>): Promise<boolean> {
    const my = ++seq.current
    setBusyItem(itemId)
    setError(null)
    try {
      const next = await fn()
      if (my === seq.current) setReview(next)
      return true
    } catch (err) {
      if (my === seq.current) setError(errorText(err))
      return false
    } finally {
      setBusyItem(null)
    }
  }

  const complete = (current: Review) =>
    void run(PANEL_BUSY, async () => {
      const done = await completeReview(current.id)
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

  return (
    <section className="review-panel review-panel-wide" aria-label="통합 전 사전 검토">
      <div className="review-head">
        <h2>
          통합 전 사전 검토{review ? ` #${review.id}` : ''}
          {review && <span className="wk-head-sub">{STATUS_TEXT[review.status]}</span>}
        </h2>
        {review && (
          <NavLink className="wk-permalink" href={`/reviews?review=${review.id}`}>
            이 검토 주소
          </NavLink>
        )}
        <button type="button" className="mapping-confirm" onClick={onClose}>
          닫기
        </button>
      </div>
      {review && !loading && <WorkflowSteps steps={reviewSteps(review, runsDone)} />}

      {loading && <ProgressPanel stages={REVIEW_STAGES} title="검토 항목을 만드는 중 (LLM 판정이 필요한 열이 있으면 수십 초)" />}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {review && !loading && (
        <>
          <ScoreHeader
            review={review}
            busy={busyItem !== null}
            onRecheck={() => void recheck(review)}
            onComplete={() => complete(review)}
          />
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
              pendingCheck={(item) => waitingRecheck(review, item)}
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
                  void run(PANEL_BUSY, () => setRememberFormat(review.id, e.target.checked)).then((saved) => {
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
                {RECHECK_LABEL}
              </button>
              <button
                type="button"
                className="mapping-run"
                disabled={!review.can_complete || busyItem !== null || !review.can_edit}
                onClick={() => complete(review)}
              >
                {review.status === 'ready' ? '검토 완료됨' : '검토 완료'}
              </button>
            </div>
            <FooterWhy review={review} busy={busyItem !== null} />
            {message && (
              <p className="mapping-message" role="status">
                {message}
              </p>
            )}
            {review.status === 'ready' && review.can_edit && (
              <IntegrationResult
                onRuns={(runs) => setRunsDone(runs.filter((r) => r.status === 'done').length)}
                reviewId={review.id}
                defaultIncludeUnmapped={review.include_unmapped_default}
                piiIncluded={review.items.some(
                  (i) => i.kind === 'pii' && !review.files.some((f) => f.id === i.file_id && f.excluded),
                )}
                blocked={
                  review.needs_recheck
                    ? `이 검토의 검사 결과를 지금 열 매핑에 쓸 수 없어 새 결과를 만들 수 없습니다 — '${RECHECK_LABEL}'로 지금 매핑을 검사하세요. 이미 만든 결과는 아래에서 계속 내려받을 수 있습니다.`
                    : null
                }
                onFailed={() => {
                  setMessage(null) // '검토 완료 — 통합 준비가 끝났습니다' 같은 지난 안내가 바뀐 상태와 섞이지 않게
                  void refresh()
                }}
              />
            )}
            {/* 원본 교체는 '다시 검사'에만 쓰인다 — 다시 검사할 수 없는 조회 전용·대체된 검토에는 보이지 않는다 */}
            {review.can_edit && review.status !== 'superseded' && (
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
            )}
          </div>
        </>
      )}
      {reasonItem && <ReasonDialog item={reasonItem} onClose={() => setReasonItem(null)} />}
    </section>
  )
}

const STATUS_TEXT: Record<Review['status'], string> = {
  in_review: '검토 중',
  ready: '검토 완료',
  superseded: '다시 검사로 대체됨',
}

/** 단계 안내: 검토·결과 상태는 서버가 준 검토 상태와 실행 기록으로 정한다 */
function reviewSteps(review: Review, runsDone: number): Step[] {
  const openMapping = review.items.filter((i) => i.category === 'pending' && i.kind === 'mapping' && !i.decision).length
  const ready = review.status === 'ready' && !review.needs_recheck
  const reviewDetail = review.needs_recheck
    ? '다시 검사 필요'
    : review.status === 'superseded'
      ? '대체된 검토'
      : review.counts.block
        ? `차단 ${review.counts.block}`
        : review.counts.pending_open
          ? `확인 필요 ${review.counts.pending_open}`
          : review.status === 'ready'
            ? '검토 완료'
            : '검토 완료를 누르면 끝'
  return [
    { key: 'files', state: 'done', detail: `${review.files.length}개 파일`, href: '/' },
    {
      key: 'mapping',
      state: openMapping ? 'attention' : 'done',
      detail: openMapping ? `확인할 열 ${openMapping}` : '검토에서 확인할 열 없음',
      href: review.files.length ? `/mapping?file=${review.files[0].id}` : '/mapping',
    },
    { key: 'review', current: true, state: ready ? 'done' : 'attention', detail: reviewDetail },
    {
      key: 'result',
      state: runsDone > 0 ? 'done' : 'todo',
      detail: runsDone > 0 ? `결과 ${runsDone}개` : ready ? '통합 결과 만들기' : '검토 완료 뒤',
      href: '/runs',
    },
  ]
}

/** 다시 검사 이유에 이 항목의 열이 들어 있으면, 승인은 저장됐지만 아직 검사에 반영되지 않은 상태 */
function waitingRecheck(review: Review, item: ReviewItem): boolean {
  if (!review.needs_recheck || !item.decision || !item.column) return false
  const col = `'${item.column}' 열`
  return (review.recheck_reasons ?? []).some((r) => r.includes(col) && (!item.file_name || r.includes(item.file_name)))
}

function ScoreHeader({
  review,
  busy,
  onRecheck,
  onComplete,
}: {
  review: Review
  busy: boolean
  onRecheck: () => void
  onComplete: () => void
}) {
  // 남이 올린 파일의 매핑·개인정보 항목 (서버가 판단한 조회 전용 항목)
  const readOnlyFiles = [
    ...new Set(
      review.items.filter((i) => i.category === 'pending' && i.can_change === false).map((i) => i.file_name ?? `#${i.file_id}`),
    ),
  ]
  const stale = Boolean(review.needs_recheck)
  // 다시 검사가 필요하면 지난 검사 점수를 '통과'처럼 보이게 하지 않는다 (색 없이, 지난 점수라고 적음)
  const level = stale
    ? 'is-stale'
    : review.counts.block
      ? 'is-block'
      : review.counts.pending_open
        ? 'is-pending'
        : 'is-ok'
  const target = review.status === 'ready' ? '통합' : '완료·통합'
  const latest = review.latest_id ?? review.child_id
  return (
    <>
      {review.status === 'superseded' && (
        <Banner tone="muted" icon="recheck" title="다시 검사로 대체된 이전 검토" live="status">
          <p>이 검토의 결정은 다시 검사한 검토로 이어졌습니다. 이 화면은 조회만 할 수 있습니다.</p>
          {latest && (
            <p>
              <NavLink href={`/reviews?review=${latest}`}>최신 검토 #{latest}로 이동</NavLink>
            </p>
          )}
        </Banner>
      )}
      {stale && (
        <Banner
          tone="recheck"
          icon="recheck"
          title="다시 검사 필요"
          live="status"
          actions={
            review.can_edit && review.status !== 'superseded' ? (
              <button type="button" className="mapping-run" disabled={busy} onClick={onRecheck}>
                {RECHECK_LABEL}
              </button>
            ) : undefined
          }
        >
          <p>이전 검사 결과로는 지금 열 매핑을 {target}할 수 없습니다. 승인한 내용은 저장되어 있으며, 다시 검사하면 이어받습니다.</p>
          {(review.recheck_reasons?.length ?? 0) > 0 && (
            <details className="review-recheck" open>
              <summary>다시 검사가 필요한 이유 {review.recheck_reasons?.length}건</summary>
              <ul>
                {review.recheck_reasons?.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </details>
          )}
          <p className="mw-muted">이미 만든 통합 결과는 계속 조회·내려받을 수 있습니다 (실행 기록·아래 결과 목록).</p>
        </Banner>
      )}
      <div className={`review-score ${level}`}>
        {/* 일반 div의 aria-label 대신 보이지 않는 글자로 무엇의 점수인지 둔다. 다시 검사 전이면 보이는 '지난 검사 점수' 안내가 이어진다 */}
        <div className="review-score-value">
          <span className="sr-only">데이터 품질 점수 </span>
          <strong>{review.score}</strong>
          <span>/ 100</span>
          {stale && <span className="wk-score-note">지난 검사 점수 · 지금 매핑 기준 아님</span>}
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
          {/* 다시 검사가 필요하면 위의 띠가 상태·이유·행동을 모두 보여 주므로 같은 문장을 되풀이하지 않는다 */}
          {!stale && <p className="review-summary">{review.summary}</p>}
          <StatusCards
            label="검토 상태 요약"
            cards={[
              { key: 'block', label: '차단', count: review.counts.block, tone: 'block', icon: 'block', hint: review.counts.block ? '원본을 고친 뒤 다시 검사' : undefined },
              {
                key: 'pending',
                label: '확인 필요 (남음/전체)',
                count: `${review.counts.pending_open}/${review.counts.pending}`,
                tone: 'review',
                icon: 'alert',
              },
              { key: 'warning', label: '경고 (통합은 진행)', count: review.counts.warning, tone: 'warn', icon: 'info' },
              { key: 'auto', label: '자동 적용', count: review.counts.auto, tone: 'auto', icon: 'check' },
              ...(stale ? [{ key: 'recheck', label: '다시 검사 필요', count: review.recheck_reasons?.length ?? 1, tone: 'recheck' as const, icon: 'recheck' as const }] : []),
            ]}
          />
          <NextAction review={review} busy={busy} onComplete={onComplete} />
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
    </>
  )
}

/** 아래 버튼(다시 검사·검토 완료)을 누를 수 없는 이유 — 버튼 바로 옆에 (서버의 can_edit·can_complete·상태·건수만 씀) */
function FooterWhy({ review, busy }: { review: Review; busy: boolean }) {
  if (busy) return null
  let text: string
  if (!review.can_edit) text = '조회 전용 — 검토를 만든 사람만 다시 검사·완료할 수 있습니다.'
  else if (review.status === 'superseded') text = '새 검토로 대체된 검토입니다 — 새 검토에서 이어 하세요.'
  else if (review.status !== 'in_review' || review.can_complete) return null
  else if (review.needs_recheck) text = "'검토 완료'를 누를 수 없음 — 먼저 다시 검사하세요."
  else if (review.counts.block) text = `'검토 완료'를 누를 수 없음 — 차단 ${review.counts.block}건: 원본을 고친 뒤 다시 검사하세요.`
  else if (review.counts.pending_open) text = `'검토 완료'를 누를 수 없음 — 확인 필요 ${review.counts.pending_open}건을 승인 또는 거부하세요.`
  else return null
  return (
    <p className="wk-why" role="note">
      {text}
    </p>
  )
}

/** 다음 행동 한 줄 (서버의 can_complete·needs_recheck·상태만 쓴다 — 화면에서 따로 추정하지 않음) */
function NextAction({
  review,
  busy,
  onComplete,
}: {
  review: Review
  busy: boolean
  onComplete: () => void
}) {
  // 다시 검사가 필요하면 위의 '다시 검사 필요' 띠에 행동 버튼이 있다
  if (!review.can_edit || review.status === 'superseded' || review.needs_recheck) return null
  let text: string
  let action: React.ReactNode = null
  if (review.status === 'ready') {
    text = "다음: 아래 '통합 결과 만들기'로 결과 엑셀을 만드세요."
  } else if (review.counts.block) {
    text = '다음: 차단 항목의 원본 파일을 고친 뒤 다시 검사하세요 (아래 원본 교체).'
  } else if (review.counts.pending_open) {
    text = `다음: 확인 필요 ${review.counts.pending_open}건을 승인 또는 거부하세요.`
  } else if (review.can_complete) {
    text = '다음: 검토를 완료하면 통합 결과를 만들 수 있습니다.'
    action = (
      <button type="button" className="mapping-run" disabled={busy} onClick={onComplete}>
        검토 완료
      </button>
    )
  } else {
    return null
  }
  return (
    <p className="wk-next-action" role="status">
      <span>{text}</span> {action}
    </p>
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
  pendingCheck?: (item: ReviewItem) => boolean
  busyItem: number | null
  onDecide: (item: ReviewItem, decision: 'approved' | 'rejected', choice: string | null) => void
  onOpenReason: (item: ReviewItem) => void
  fields: StandardFieldInfo[]
}

function ItemGroup({ review, category, title, hint, pendingCheck, busyItem, onDecide, onOpenReason, fields }: GroupProps) {
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
                waiting={pendingCheck?.(i) ?? false}
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
  waiting?: boolean // 승인은 저장됐지만 다시 검사 전 (검사 결과에 아직 반영 안 됨)
  onDecide?: GroupProps['onDecide']
  onOpenReason?: (item: ReviewItem) => void
  fields?: StandardFieldInfo[]
}

function ItemRow({ item, disabled, busy, waiting, onDecide, onOpenReason, fields = [] }: RowProps) {
  const choices = choicesOf(item)
  // 후보에 없는 표준 항목 (열 매핑만)
  const others =
    item.kind === 'mapping' ? fields.filter((f) => !choices.some((c) => c.value === f.key)).map((f) => ({ value: f.key, label: f.label })) : []
  // 열 매핑은 기존 선택·제안 항목이 없으면 첫 후보를 고르지 않고 '표준 항목 선택'으로 시작한다 (직접 골라야 승인)
  const initial = item.choice ?? (item.kind === 'mapping' ? item.payload.field_key : choices[0]?.value) ?? ''
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
      {/* 차단 항목은 막는 이유가 접혀 숨지 않게 처음부터 펼친다 */}
      <details
        open={item.category === 'block'}
        className={`review-item review-compact is-${item.category}${item.decision ? ` is-${item.decision}` : ''}${item.excluded ? ' is-excluded' : ''}`}
      >
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
                decided && (
                  <span className={`review-state${waiting ? ' wk-waiting' : ''}`}>
                    {decided}
                    {waiting ? ' · 저장됨, 다시 검사 전' : ''}
                  </span>
                )
              )}
            </span>
          ) : (
            <span className="review-controls">{decided && <span className="review-state">{decided}</span>}</span>
          )}
        </summary>
        <div className="review-reason">
          {/* 접힌 줄의 위치는 한 줄로 잘려 보일 수 있다 (마우스를 올린 툴팁만으로 전하지 않음) — 펼치면 전체를 보인다 */}
          <p className="review-where-full">
            <span className="review-where-label">위치</span> {where}
          </p>
          <span className={`review-source is-${item.reason_source}`}>
            {item.reason_source === 'llm' ? 'LLM 생성 문장' : '규칙 문장'}
          </span>{' '}
          <span className={`review-reason-text${long ? ' is-clamped' : ''}`}>{item.reason}</span>
          {long && onOpenReason && (
            <button
              type="button"
              className="review-reason-more"
              // 같은 '전체 보기'가 여러 줄에 있으므로 어느 항목인지 이름에 붙인다 (보이는 글자로 시작 — 화면에 보이는 가린 값 그대로)
              aria-label={`전체 보기: ${item.before ?? '-'} → ${item.after ?? '-'} 근거`}
              onClick={() => onOpenReason(item)}
            >
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
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null // 연 '전체 보기' 버튼
    if (dialog && !dialog.open) dialog.showModal()
    return () => {
      // 닫힌 뒤 초점이 본문으로 빠졌으면 연 버튼으로 돌려준다 (브라우저가 이미 돌려줬으면 그대로)
      requestAnimationFrame(() => {
        const now = document.activeElement
        if (opener?.isConnected && (!now || now === document.body)) opener.focus()
      })
    }
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
