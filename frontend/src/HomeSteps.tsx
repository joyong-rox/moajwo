import { useEffect, useState } from 'react'
import { fetchLatestMapping, listFiles, type StoredMapping } from './api'
import type { ResultMode } from './Validation'
import { WorkflowSteps, type Step } from './Workflow'

const MAPPING_BATCH = 10 // 열 매핑 상태를 한 번에 조회하는 파일 수 (처음 여는 파일은 원본을 읽으므로 동시에 이 이상 보내지 않음)
const SETTLE_MS = 300 // 선택을 연달아 바꾸는 동안은 기다렸다가 마지막 선택만 확인한다

// 파일 하나의 확인 결과 — null은 상태를 불러오지 못함 (읽을 수 없는 파일·조회 실패)
type FileCheck = { left: number; stale: boolean } | null
// 선택 순서대로 앞에서부터 확인한 결과. 어떤 선택·방문(stamp)에서 받았는지와 함께 두고, 다르면 쓰지 않는다
type Checked = { stamp: string; items: FileCheck[] }

function summarize(m: StoredMapping): FileCheck {
  let left = 0
  for (const s of m.sheets)
    for (const c of s.columns) if (['review', 'unresolved', 'unanalyzed'].includes(c.status)) left += 1
  return { left, stale: Boolean(m.stale) }
}

/** 홈의 단계 안내: 올린 파일·선택한 파일의 저장된 매핑으로 정한다 (방문만으로 완료 처리하지 않음).
 * 열 매핑은 선택한 파일을 모두 확인했을 때만 '완료' — 앞 몇 개만 확인했으면 '일부 확인'으로 범위를 보인다.
 * 상태 조회는 아직 확인하지 않은 파일만, 한 번에 MAPPING_BATCH개까지 보낸다 (확인한 파일은 다시 조회하지 않음).
 * refreshKey는 홈으로 돌아올 때·파일을 올릴 때 바뀐다 → 그때는 처음부터 다시 확인한다 (다른 화면에서 바꾼 매핑).
 * 선택이나 방문이 바뀌면 진행 중인 조회를 취소하고, 이전 결과를 새 기준의 완료 근거로 쓰지 않는다. */
function HomeSteps({ refreshKey, selected, mode = 'table' }: { refreshKey: number; selected: number[]; mode?: ResultMode }) {
  const [fileCount, setFileCount] = useState<number | null>(null)
  const [result, setResult] = useState<Checked | null>(null)
  // 확인하기로 한 범위 (앞에서부터 몇 개) — 선택이 바뀌면 처음 10개부터, 같은 선택이면 홈에 돌아와도 유지
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
  const stamp = `${key}|${refreshKey}`
  const total = selected.length
  const target = Math.min(total, budget.key === key ? budget.n : MAPPING_BATCH)
  const items = result?.stamp === stamp ? result.items : []
  const have = items.length
  useEffect(() => {
    if (!key || have >= target || mode !== 'table') return // 파일별 시트로 묶기·공통 key 연결은 열 매핑을 요구하지 않는다
    const ids = key.split(',').map(Number).slice(have, Math.min(target, have + MAPPING_BATCH))
    const controller = new AbortController()
    const timer = window.setTimeout(
      () => {
        Promise.all(ids.map((id) => fetchLatestMapping(id, controller.signal).then(summarize, () => null))).then((list) => {
          if (controller.signal.aborted) return // 선택·방문이 바뀐 뒤 도착한 응답은 버린다
          setResult((prev) => {
            const base = prev?.stamp === stamp ? prev.items : []
            return base.length === have ? { stamp, items: [...base, ...list] } : prev
          })
        })
      },
      have === 0 ? SETTLE_MS : 0,
    )
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [key, stamp, have, target, mode])

  const checking = total > 0 && have < target
  let mapping: Pick<Step, 'state' | 'detail'>
  if (!total) mapping = { state: 'todo', detail: '검사할 파일을 고르면 확인' }
  else if (have === 0) mapping = { state: 'todo', detail: `확인 중… (0/${target}개)` }
  else {
    const failed = items.filter((x) => x === null).length
    const checked = items.filter((x): x is NonNullable<FileCheck> => x !== null)
    const left = checked.reduce((n, x) => n + x.left, 0)
    const filesLeft = checked.filter((x) => x.left > 0).length
    const stale = checked.filter((x) => x.stale).length
    const scope = have < total ? `${have}/${total}개 확인 · ${total - have}개 미확인` : ''
    const problems = [
      left > 0 && `확인할 열 ${left}개 (파일 ${filesLeft}개)`,
      failed > 0 && `매핑을 불러오지 못한 파일 ${failed}개`,
      stale > 0 && `다시 분석할 파일 ${stale}개`,
    ].filter(Boolean)
    const tail = checking ? ` · 확인 중… (${have}/${target}개)` : ''
    if (problems.length) mapping = { state: 'attention', detail: [...problems, scope].filter(Boolean).join(' · ') + tail }
    else if (scope) mapping = { state: 'partial', detail: `${scope} — 확인한 파일은 남은 확인 없음${tail}` }
    else mapping = { state: 'done', detail: `${total}개 모두 남은 확인 없음` }
  }
  const remaining = !checking && have > 0 ? total - have : 0

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
  if (mode === 'keyjoin') {
    // 공통 key로 연결하기: 파일 선택 → 취합 방식 → key 열·가져올 열·범위 → 연결 결과·문제 미리보기 → 엑셀 만들기 (설정부터는 다음 화면)
    return (
      <WorkflowSteps
        steps={[
          steps[0],
          { key: 'mode', state: 'done', detail: '공통 key 연결' },
          { key: 'settings', state: 'todo', detail: '다음 화면 — key 열·가져올 열·범위' },
          { key: 'compose', state: 'todo', detail: '연결 결과·문제 미리보기' },
          { key: 'make', state: 'todo', detail: '실행 기록', href: '/runs' },
        ]}
      />
    )
  }
  if (mode === 'sheets') {
    // 파일별 시트로 묶기: 파일 선택 → 취합 방식 → 필요한 설정 → 결과 구성 확인 → 엑셀 만들기 (설정부터는 다음 화면)
    return (
      <WorkflowSteps
        steps={[
          steps[0],
          { key: 'mode', state: 'done', detail: '파일별 시트' },
          { key: 'settings', state: 'todo', detail: '다음 화면 — 넣을 시트·통계 요약(선택)' },
          { key: 'compose', state: 'todo', detail: '결과 시트 이름·순서·원본' },
          { key: 'make', state: 'todo', detail: '실행 기록', href: '/runs' },
        ]}
      />
    )
  }
  return (
    <>
      <WorkflowSteps steps={steps} />
      {remaining > 0 && (
        <p className="wk-steps-more">
          <button
            type="button"
            className="login-link"
            onClick={() => setBudget({ key, n: Math.min(total, have + MAPPING_BATCH) })}
          >
            다음 {Math.min(MAPPING_BATCH, remaining)}개 파일도 확인 (미확인 {remaining}개)
          </button>
        </p>
      )}
    </>
  )
}

export default HomeSteps
