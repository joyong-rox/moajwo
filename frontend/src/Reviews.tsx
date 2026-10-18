import { useCallback, useEffect, useMemo, useState } from 'react'
import { listReviews, runDownloadUrl, type Review, type ReviewListItem } from './api'
import { isManager, useAuth } from './authContext'
import ReviewPanel from './ReviewPanel'
import { navigate, replaceSearch, useSearch } from './router'
import { formatTime } from './runFormat'
import { Icon, StatusCards, type IconName, type Tone } from './Workflow'

type Filter = 'open' | 'recheck' | 'ready' | 'all'

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
  const [rows, setRows] = useState<ReviewListItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all') // 처음엔 대체되지 않은 검토 전부 (완료·다시 검사 필요도 바로 보이게)
  const [withOld, setWithOld] = useState(false)

  const load = useCallback(() => {
    let alive = true
    listReviews(withOld)
      .then((list) => {
        if (!alive) return
        setRows(list)
        setError(null)
      })
      .catch((err: unknown) => alive && setError(`검토 목록을 불러오지 못했습니다: ${err instanceof Error ? err.message : err}`))
    return () => {
      alive = false
    }
  }, [withOld])

  useEffect(() => (openId === null ? load() : undefined), [load, openId])

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { open: 0, recheck: 0, ready: 0, all: rows?.length ?? 0 }
    for (const r of rows ?? []) {
      if (r.status === 'superseded') continue
      if (r.needs_recheck) c.recheck += 1
      if (r.status === 'in_review') c.open += 1
      if (r.status === 'ready') c.ready += 1
    }
    return c
  }, [rows])
  const shown = (rows ?? []).filter((r) =>
    filter === 'all'
      ? true
      : filter === 'recheck'
        ? r.needs_recheck
        : filter === 'open'
          ? r.status === 'in_review'
          : r.status === 'ready',
  )

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
        <button type="button" className="mapping-confirm" onClick={() => navigate('/reviews')}>
          ← 검토 목록으로
        </button>
        <ReviewPanel key={openId} reviewId={openId} onClose={() => navigate('/reviews')} onLoaded={onLoaded} />
      </section>
    )
  }

  return (
    <section className="wk wk-reviews runs" aria-label="검토 목록">
      <div className="runs-head">
        <h2>검토 목록</h2>
        <button type="button" className="mapping-confirm" onClick={() => load()}>
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
          { key: 'all', label: FILTER_TEXT.all, count: counts.all, tone: 'info', icon: 'list' },
          { key: 'open', label: FILTER_TEXT.open, count: counts.open, tone: 'review', icon: 'clock' },
          { key: 'recheck', label: FILTER_TEXT.recheck, count: counts.recheck, tone: 'recheck', icon: 'recheck' },
          { key: 'ready', label: FILTER_TEXT.ready, count: counts.ready, tone: 'ok', icon: 'check' },
        ]}
      />
      <label className="review-remember">
        <input type="checkbox" checked={withOld} onChange={(e) => setWithOld(e.target.checked)} />
        다시 검사로 대체된 이전 검토도 보기
      </label>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {rows === null && !error && <p className="mw-muted">불러오는 중…</p>}
      {rows !== null && shown.length === 0 && <p className="upload-drop-hint">해당하는 검토가 없습니다.</p>}
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
                      ) : r.run_count ? (
                        `실행 ${r.run_count}개 (결과 파일 없음)`
                      ) : (
                        '아직 없음'
                      )}
                    </td>
                    <td data-label="열기">
                      <button type="button" className="mapping-confirm" onClick={() => navigate(`/reviews?review=${target}`)}>
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
    </section>
  )
}

export default Reviews
