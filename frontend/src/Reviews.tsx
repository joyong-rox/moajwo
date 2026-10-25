import { useCallback, useEffect, useRef, useState } from 'react'
import {
  countReviews,
  listReviews,
  runDownloadUrl,
  type Review,
  type ReviewCounts,
  type ReviewFilter,
  type ReviewListItem,
} from './api'
import { isManager, useAuth } from './authContext'
import ReviewPanel from './ReviewPanel'
import { navigate, replaceSearch, useSearch } from './router'
import { formatTime } from './runFormat'
import { Icon, StatusCards, type IconName, type Tone } from './Workflow'

type Filter = ReviewFilter
const PAGE = 50 // 한 번에 불러오는 검토 수 (더 오래된 검토는 '더 보기'로 이어서)

const FILTER_TEXT: Record<Filter, string> = {
  open: '검토 중',
  recheck: '다시 검사 필요',
  ready: '검토 완료',
  all: '전체',
}

function stateOf(r: ReviewListItem): { text: string; tone: Tone; icon: IconName } {
  if (r.status === 'superseded') return { text: '대체됨', tone: 'muted', icon: 'recheck' }
  if (r.needs_recheck) return { text: '다시 검사 필요', tone: 'recheck', icon: 'recheck' }
  if (r.status === 'ready') return { text: '검토 완료', tone: 'ok', icon: 'check' }
  return { text: '검토 중', tone: 'review', icon: 'clock' }
}

// 불러온 목록은 어느 조건(key)으로 받았는지와 함께 둔다 — 조건이 바뀌면 그 목록을 쓰지 않는다
type Loaded = { key: string; items: ReviewListItem[]; next: number | null; error: string | null }

function totalFor(filter: Filter, withOld: boolean, c: ReviewCounts): number {
  if (filter === 'all') return withOld ? c.total : c.live
  return filter === 'open' ? c.open : filter === 'ready' ? c.ready : c.recheck
}

function reviewIdFrom(search: string): number | null {
  const id = Number(new URLSearchParams(search).get('review'))
  return Number.isInteger(id) && id > 0 ? id : null
}

/** 검토 목록: 결과를 만들기 전의 수동 검토도 찾아 이어간다. 주소 /reviews?review=번호 로 같은 검토를 다시 연다. */
function Reviews() {
  const { me } = useAuth()
  const showUser = isManager(me.role)
  const search = useSearch()
  const openId = reviewIdFrom(search)
  const [filter, setFilter] = useState<Filter>('all') // 처음엔 대체되지 않은 검토 전부 (완료·다시 검사 필요도 바로 보이게)
  const [withOld, setWithOld] = useState(false)
  const [reload, setReload] = useState(0)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [more, setMore] = useState<{ key: string; before: number } | null>(null)
  const [counts, setCounts] = useState<ReviewCounts | null>(null)
  const [countsError, setCountsError] = useState(false)
  const listSeq = useRef(0) // 마지막 요청만 화면에 반영한다 (늦게 도착한 이전 조건의 응답은 버림)
  const countSeq = useRef(0)
  const key = `${filter}|${withOld}|${reload}`

  // 상태 조건은 서버에서 먼저 걸고 첫 쪽부터 받는다 — 조건·새로고침이 바뀌거나 상세에서 돌아오면 다시
  useEffect(() => {
    if (openId !== null) return
    const mine = ++listSeq.current
    listReviews(filter, withOld, null, PAGE)
      .then((page) => {
        if (listSeq.current === mine) setLoaded({ key, items: page.items, next: page.next_before, error: null })
      })
      .catch((err: unknown) => {
        if (listSeq.current !== mine) return
        const error = `검토 목록을 불러오지 못했습니다: ${err instanceof Error ? err.message : err}`
        setLoaded({ key, items: [], next: null, error })
      })
  }, [filter, withOld, key, openId])

  // 카드 숫자: 볼 수 있는 검토 전체의 건수 (불러온 쪽과 상관없음)
  useEffect(() => {
    if (openId !== null) return
    const mine = ++countSeq.current
    countReviews()
      .then((c) => {
        if (countSeq.current !== mine) return
        setCounts(c)
        setCountsError(false)
      })
      .catch(() => countSeq.current === mine && setCountsError(true))
  }, [reload, openId])

  const current = loaded?.key === key ? loaded : null
  const loadingMore = current !== null && more?.key === key && more.before === current.next

  function loadMore() {
    if (current === null || current.next === null || loadingMore) return
    const mine = listSeq.current
    const before = current.next
    setMore({ key, before })
    listReviews(filter, withOld, before, PAGE)
      .then((page) => {
        if (listSeq.current !== mine) return
        setLoaded((prev) => {
          if (prev === null || prev.key !== key || prev.next !== before) return prev
          const seen = new Set(prev.items.map((r) => r.id))
          return { ...prev, items: [...prev.items, ...page.items.filter((r) => !seen.has(r.id))], next: page.next_before }
        })
      })
      .catch((err: unknown) => {
        if (listSeq.current !== mine) return
        setLoaded((prev) =>
          prev && prev.key === key
            ? { ...prev, error: `더 불러오지 못했습니다: ${err instanceof Error ? err.message : err}` }
            : prev,
        )
      })
      .finally(() => setMore((m) => (m?.key === key && m.before === before ? null : m)))
  }

  const shown = current?.items ?? []
  const total = counts ? totalFor(filter, withOld, counts) : null

  // 열어 둔 검토가 다시 검사로 바뀌면 주소도 새 검토로 (새로고침하면 최신 검토가 열림)
  const onLoaded = useCallback(
    (review: Review) => {
      if (review.id !== openId) replaceSearch(`?review=${review.id}`)
    },
    [openId],
  )

  if (openId !== null) {
    return (
      <section className="wk wk-reviews" aria-label="검토">
        {/* 검색 조건(?review=)을 지우고 목록으로 — 같은 경로라도 이동한다 */}
        <button type="button" className="mapping-confirm" onClick={() => navigate('/reviews', { clearSearch: true, focus: { to: 'return' } })}>
          ← 검토 목록으로
        </button>
        <ReviewPanel
          key={openId}
          reviewId={openId}
          onClose={() => navigate('/reviews', { clearSearch: true, focus: { to: 'return' } })}
          onLoaded={onLoaded}
        />
      </section>
    )
  }

  return (
    <section className="wk wk-reviews runs" aria-label="검토 목록">
      <div className="runs-head">
        <h2>검토 목록</h2>
        <button type="button" className="mapping-confirm" onClick={() => setReload((v) => v + 1)}>
          새로고침
        </button>
      </div>
      <p className="upload-drop-hint">
        {showUser ? '전체 사용자의' : '내'} 통합 전 검토를 최신 순으로 보여 줍니다. 결과를 아직 만들지 않은 검토도 여기서
        이어갈 수 있고, 주소(/reviews?review=번호)로 같은 검토를 다시 열 수 있습니다.
        {showUser && ' 다른 사용자의 검토는 조회만 할 수 있습니다.'}
      </p>
      <StatusCards
        label="검토 상태 (누르면 그 상태만 보기)"
        picked={filter}
        onPick={(key) => setFilter(key as Filter)}
        cards={[
          { key: 'all', label: FILTER_TEXT.all, count: counts ? totalFor('all', withOld, counts) : '…', tone: 'info', icon: 'list' },
          { key: 'open', label: FILTER_TEXT.open, count: counts?.open ?? '…', tone: 'review', icon: 'clock' },
          {
            key: 'recheck',
            label: FILTER_TEXT.recheck,
            count: counts?.recheck ?? '…',
            tone: 'recheck',
            icon: 'recheck',
          },
          { key: 'ready', label: FILTER_TEXT.ready, count: counts?.ready ?? '…', tone: 'ok', icon: 'check' },
        ]}
      />
      <p className="mw-muted wk-count-scope">
        {countsError
          ? '상태별 건수를 불러오지 못했습니다 — 새로고침하세요. 목록은 아래에서 계속 볼 수 있습니다.'
          : `카드 숫자는 볼 수 있는 검토 전체 기준입니다(대체된 검토 ${withOld ? '포함' : '제외'}) — 아래 목록에 불러온 범위와 상관없습니다.`}
      </p>
      <label className="review-remember">
        <input type="checkbox" checked={withOld} onChange={(e) => setWithOld(e.target.checked)} />
        다시 검사로 대체된 이전 검토도 보기
      </label>
      {current?.error && (
        <p className="upload-error" role="alert">
          {current.error}
        </p>
      )}
      {current === null && <p className="mw-muted">불러오는 중…</p>}
      {/* '없음'은 끝까지 살펴봤을 때만 — 아직 보지 않은 더 오래된 검토가 있으면 그렇게 말한다 */}
      {current !== null && !current.error && shown.length === 0 && current.next === null && (
        <p className="upload-drop-hint">해당하는 검토가 없습니다.</p>
      )}
      {current !== null && shown.length === 0 && current.next !== null && (
        <p className="upload-drop-hint">
          최근 검토부터 살펴봤지만 아직 찾지 못했습니다. 더 오래된 검토가 남아 있습니다 — '더 찾아보기'를 누르세요.
        </p>
      )}
      {shown.length > 0 && (
        <div className="runs-scroll">
          <table className="runs-table wk-review-table">
            <thead>
              <tr>
                <th scope="col">번호</th>
                <th scope="col">상태</th>
                <th scope="col">파일</th>
                {showUser && <th scope="col">만든 사람</th>}
                <th scope="col">만든 때</th>
                <th scope="col">결과</th>
                <th scope="col">열기</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const state = stateOf(r)
                const target = r.status === 'superseded' ? r.latest_id : r.id
                return (
                  <tr key={r.id}>
                    <td data-label="번호">#{r.id}</td>
                    <td data-label="상태">
                      <span className={`wk-badge tone-${state.tone}`}>
                        <Icon name={state.icon} /> {state.text}
                      </span>
                    </td>
                    <td data-label="파일" className="wk-files-cell">
                      {r.file_names.join(', ')}
                      {r.file_count > r.file_names.length && ` 외 ${r.file_count - r.file_names.length}개`}
                      {r.automation_name && <span className="mw-muted mw-block">자동화 '{r.automation_name}'</span>}
                    </td>
                    {showUser && <td data-label="만든 사람">{r.created_by_name ?? '(계정 도입 전)'}</td>}
                    <td data-label="만든 때">{formatTime(r.created_at)}</td>
                    <td data-label="결과">
                      {r.last_run_id ? (
                        <a href={runDownloadUrl(r.last_run_id)}>결과 #{r.last_run_id} 내려받기</a>
                      ) : r.pending_count ? (
                        `실행 ${r.run_count}개 (만드는 중·결과 확인 필요 ${r.pending_count}개)`
                      ) : r.run_count ? (
                        `실행 ${r.run_count}개 (결과 파일 없음)`
                      ) : (
                        '아직 없음'
                      )}
                    </td>
                    <td data-label="열기">
                      <button
                        type="button"
                        className="mapping-confirm"
                        data-focus-key={`review-row-${r.id}`}
                        onClick={() => navigate(`/reviews?review=${target}`, { focus: { to: 'detail', returnKey: `review-row-${r.id}` } })}
                      >
                        {r.status === 'superseded'
                          ? `최신 #${r.latest_id} 열기`
                          : r.can_edit && r.status === 'in_review'
                            ? '검토 이어서 하기'
                            : '검토 보기'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {current !== null && (shown.length > 0 || current.next !== null) && (
        <div className="wk-list-more">
          <span role="status">
            {shown.length}개 표시{total !== null && ` · 전체 ${total}개`}
            {current.next === null ? ' · 끝까지 봤습니다' : ''}
          </span>
          {current.next !== null && (
            <button type="button" className="mapping-confirm" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? '불러오는 중…' : filter === 'recheck' ? '더 찾아보기' : `더 보기 (${PAGE}개씩)`}
            </button>
          )}
        </div>
      )}
    </section>
  )
}

export default Reviews
