import { useEffect, useState } from 'react'
import { fetchLatestMapping, listFiles } from './api'
import { WorkflowSteps, type Step } from './Workflow'

const MAPPING_BATCH = 10 // 열 매핑 상태를 한 번에 확인하는 선택 파일 수 (처음 여는 파일은 원본을 읽으므로 요청 수를 제한)

// 확인한 결과는 어떤 선택·범위·방문(stamp)에서 받았는지와 함께 둔다 — 다르면 쓰지 않고 '확인 중'
type Checked = { stamp: string; checked: number; left: number; filesLeft: number; unreadable: number; stale: number }

/** 홈의 단계 안내: 올린 파일·선택한 파일의 저장된 매핑으로 정한다 (방문만으로 완료 처리하지 않음).
 * 열 매핑은 선택한 파일을 모두 확인했을 때만 '완료' — 앞 몇 개만 확인했으면 '일부 확인'으로 범위를 보인다.
 * refreshKey는 홈으로 돌아올 때·파일을 올릴 때 바뀐다 → 다른 화면에서 바꾼 매핑을 다시 확인한다. */
function HomeSteps({ refreshKey, selected }: { refreshKey: number; selected: number[] }) {
  const [fileCount, setFileCount] = useState<number | null>(null)
  const [result, setResult] = useState<Checked | null>(null)
  const [budget, setBudget] = useState<{ key: string; n: number }>({ key: '', n: MAPPING_BATCH })

  useEffect(() => {
    let alive = true
    listFiles()
      .then((list) => alive && setFileCount(list.length))
      .catch(() => alive && setFileCount(null))
    return () => {
      alive = false
    }
  }, [refreshKey])

  const key = selected.join(',')
  const limit = Math.min(selected.length, budget.key === key ? budget.n : MAPPING_BATCH) // 선택이 바뀌면 처음 10개부터
  const stamp = `${key}|${limit}|${refreshKey}`
  useEffect(() => {
    if (!key) return
    let alive = true
    const ids = key.split(',').map(Number).slice(0, limit)
    Promise.all(ids.map((id) => fetchLatestMapping(id).catch(() => null)))
      .then((list) => {
        if (!alive) return // 늦게 도착한 이전 선택·이전 방문의 응답은 쓰지 않는다
        let left = 0
        let filesLeft = 0
        let unreadable = 0 // 상태를 불러오지 못한 파일 (읽을 수 없는 파일·조회 실패) — 확인한 것으로 세지 않는다
        let stale = 0 // 파일 구성이 분석 때와 달라 다시 분석해야 하는 파일
        for (const m of list) {
          if (!m) {
            unreadable += 1
            continue
          }
          if (m.stale) stale += 1
          let n = 0
          for (const s of m.sheets)
            for (const c of s.columns) if (['review', 'unresolved', 'unanalyzed'].includes(c.status)) n += 1
          left += n
          if (n > 0) filesLeft += 1
        }
        setResult({ stamp: `${key}|${ids.length}|${refreshKey}`, checked: ids.length, left, filesLeft, unreadable, stale })
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [key, limit, refreshKey])

  const total = selected.length
  const current = total > 0 && result?.stamp === stamp ? result : null
  let mapping: Pick<Step, 'state' | 'detail'>
  if (!total) mapping = { state: 'todo', detail: '검사할 파일을 고르면 확인' }
  else if (!current) mapping = { state: 'todo', detail: `확인 중… (${limit}/${total}개)` }
  else {
    const scope = current.checked < total ? `${current.checked}/${total}개 확인 · ${total - current.checked}개 미확인` : ''
    const problems = [
      current.left > 0 && `확인할 열 ${current.left}개 (파일 ${current.filesLeft}개)`,
      current.unreadable > 0 && `매핑을 불러오지 못한 파일 ${current.unreadable}개`,
      current.stale > 0 && `다시 분석할 파일 ${current.stale}개`,
    ].filter(Boolean)
    if (problems.length) mapping = { state: 'attention', detail: [...problems, scope].filter(Boolean).join(' · ') }
    else if (scope) mapping = { state: 'partial', detail: `${scope} — 확인한 파일은 남은 확인 없음` }
    else mapping = { state: 'done', detail: `${total}개 모두 남은 확인 없음` }
  }
  const remaining = current ? total - current.checked : 0

  const steps: Step[] = [
    {
      key: 'files',
      current: true,
      state: selected.length ? 'done' : fileCount === 0 ? 'attention' : 'todo',
      detail:
        fileCount === null
          ? undefined
          : !fileCount
            ? '파일을 올리세요'
            : selected.length
              ? `올린 파일 ${fileCount}개 · 선택 ${selected.length}개`
              : `올린 파일 ${fileCount}개 · 아래에서 검사할 파일을 고르세요`,
    },
    { key: 'mapping', ...mapping, href: selected.length ? `/mapping?file=${selected[0]}` : '/mapping' },
    { key: 'review', state: 'todo', detail: "아래 '사전 검토 열기'" },
    { key: 'result', state: 'todo', detail: '실행 기록·검토 목록', href: '/runs' },
  ]
  return (
    <>
      <WorkflowSteps steps={steps} />
      {remaining > 0 && (
        <p className="wk-steps-more">
          <button
            type="button"
            className="login-link"
            onClick={() => setBudget({ key, n: Math.min(total, limit + MAPPING_BATCH) })}
          >
            다음 {Math.min(MAPPING_BATCH, remaining)}개 파일도 확인 (미확인 {remaining}개)
          </button>
        </p>
      )}
    </>
  )
}

export default HomeSteps
