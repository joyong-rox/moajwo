import { useEffect, useState } from 'react'
import { fetchLatestMapping, listFiles } from './api'
import { WorkflowSteps, type Step } from './Workflow'

const MAPPING_SAMPLE = 10 // 열 매핑 상태를 확인하는 선택 파일 수 (요청 수를 제한)

/** 홈의 단계 안내: 올린 파일·선택한 파일의 저장된 매핑으로 정한다 (방문만으로 완료 처리하지 않음). */
function HomeSteps({ refreshKey, selected }: { refreshKey: number; selected: number[] }) {
  const [fileCount, setFileCount] = useState<number | null>(null)
  const [mapping, setMapping] = useState<{
    ids: string
    left: number
    unreadable: number
    checked: number
  } | null>(null)

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
  useEffect(() => {
    if (!key) return
    let alive = true
    const ids = key.split(',').map(Number).slice(0, MAPPING_SAMPLE)
    Promise.all(ids.map((id) => fetchLatestMapping(id).catch(() => null)))
      .then((list) => {
        if (!alive) return // 늦게 도착한 이전 선택의 응답은 쓰지 않는다
        let left = 0
        let unreadable = 0 // 매핑을 불러오지 못한 파일 (읽을 수 없는 파일 등) — '남은 확인 없음'으로 세지 않는다
        for (const m of list) {
          if (!m) {
            unreadable += 1
            continue
          }
          for (const s of m.sheets)
            for (const c of s.columns) if (['review', 'unresolved', 'unanalyzed'].includes(c.status)) left += 1
        }
        setMapping({ ids: key, left, unreadable, checked: ids.length })
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [key])

  const current = selected.length > 0 && mapping?.ids === key ? mapping : null
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
    {
      key: 'mapping',
      state: !current ? 'todo' : current.left + current.unreadable > 0 ? 'attention' : 'done',
      detail: !selected.length
        ? '검사할 파일을 고르면 확인'
        : !current
          ? '확인 중…'
          : current.unreadable > 0
            ? `매핑을 불러오지 못한 파일 ${current.unreadable}개`
            : current.left > 0
              ? `확인할 열 ${current.left}개${selected.length > current.checked ? ` (앞 ${current.checked}개 파일 기준)` : ''}`
              : `남은 확인 없음${selected.length > current.checked ? ` (앞 ${current.checked}개 파일 기준)` : ''}`,
      href: selected.length ? `/mapping?file=${selected[0]}` : '/mapping',
    },
    { key: 'review', state: 'todo', detail: "아래 '사전 검토 열기'" },
    { key: 'result', state: 'todo', detail: '실행 기록·검토 목록', href: '/runs' },
  ]
  return <WorkflowSteps steps={steps} />
}

export default HomeSteps
