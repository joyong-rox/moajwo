import { useEffect, useState } from 'react'
import { integrateReview, listRuns, runDownloadUrl, saveAutomation, type Automation, type IntegrationRun } from './api'

const CHANGE_LABEL: Record<string, string> = {
  ffill: '병합 셀 채움',
  number: '숫자 변환',
  trim: '공백 정리',
  entity: '이름 정규화',
  column: '열 이름 매핑',
  other: '기타 변환',
}

/** 검토가 끝난 세션으로 통합 결과 엑셀을 만들고, 만든 결과 목록을 보여준다. */
function IntegrationResult({
  reviewId,
  defaultIncludeUnmapped = false,
}: {
  reviewId: number
  defaultIncludeUnmapped?: boolean
}) {
  const [runs, setRuns] = useState<IntegrationRun[]>([])
  // 표준 항목이 없는 열(검토에서 '해당 없음')도 원래 이름으로 결과에 넣을지 (자동화로 저장하면 이 선택도 저장)
  const [includeUnmapped, setIncludeUnmapped] = useState(defaultIncludeUnmapped)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listRuns(reviewId)
      .then((list) => !cancelled && setRuns(list))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [reviewId])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const created = await integrateReview(reviewId, includeUnmapped)
      setRuns((prev) => [created, ...prev])
    } catch (err) {
      setError(`통합 실패: ${err instanceof Error ? err.message : String(err)}`)
      listRuns(reviewId)
        .then(setRuns)
        .catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  const latest = runs.find((r) => r.status === 'done')
  const finished = runs.filter((r) => r.status !== 'review')
  return (
    <div className="integration">
      <label className="integration-option">
        <input
          type="checkbox"
          checked={includeUnmapped}
          disabled={busy}
          onChange={(e) => setIncludeUnmapped(e.target.checked)}
        />
        표준 항목이 없는 열도 원래 이름으로 포함 (검토에서 '해당 없음'으로 정한 열)
      </label>
      <div className="review-actions">
        <button type="button" className="mapping-run" disabled={busy} onClick={() => void run()}>
          {busy ? '통합 결과 만드는 중…' : '통합 결과 만들기 (엑셀)'}
        </button>
      </div>
      <p className="upload-drop-hint">
        실행할 때마다 새 결과 파일을 만듭니다 (원본은 바꾸지 않음). 시트: 통합 데이터(보정한 셀 색 표시) · 변경 내역 · 처리
        요약.
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {latest && (
        <div className="integration-latest" role="status">
          <p className="integration-title">
            결과 #{latest.id}: <a href={runDownloadUrl(latest.id)}>{latest.file_name}</a> 내려받기
          </p>
          <p className="review-counts">
            <span className="review-chip">통합 {latest.row_count}행</span>
            <span className="review-chip is-pending">셀 변경 {latest.summary.cell_change_count ?? 0}건</span>
            <span className="review-chip">열 이름 매핑 {latest.summary.column_change_count ?? 0}건</span>
            <span className="review-chip">경고 {latest.warning_count}건</span>
            {latest.summary.include_unmapped && (
              <span className="review-chip">원래 이름으로 포함한 열 {latest.summary.unmapped_columns?.length ?? 0}개</span>
            )}
          </p>
          <p className="upload-drop-hint">
            {Object.entries(latest.summary.change_counts ?? {})
              .filter(([kind]) => kind !== 'column')
              .map(([kind, count]) => `${CHANGE_LABEL[kind] ?? kind} ${count}`)
              .join(' · ') || '셀 변경 없음'}
          </p>
        </div>
      )}
      {latest && <SaveAutomation run={latest} />}
      {finished.length > 1 && (
        <details className="integration-history">
          <summary>이 검토로 만든 결과 {finished.length}개</summary>
          <ul>
            {finished.map((r) => (
              <li key={r.id}>
                #{r.id} ·{' '}
                {r.status === 'done' ? (
                  <a href={runDownloadUrl(r.id)}>{r.file_name}</a>
                ) : (
                  <span className="upload-error">실패: {r.error}</span>
                )}{' '}
                · {r.row_count}행 · 변경 {r.change_count}건
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/** 완료된 실행의 승인된 매핑 규칙·검증 조건·출력 형식을 자동화로 저장한다 (입력 파일은 저장하지 않음). */
function SaveAutomation({ run }: { run: IntegrationRun }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<Automation | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      setSaved(await saveAutomation(run.id, name.trim()))
    } catch (err) {
      setError(`저장 실패: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
  }

  if (saved) {
    return (
      <p className="integration-automation-saved" role="status">
        자동화 '{saved.name}'(으)로 저장했습니다 — 열 규칙 {saved.column_rules.length}개, 이름 연결{' '}
        {Object.keys(saved.entity_rules).length}개. 상단 '자동화' 메뉴에서 새 파일로 실행하세요.
      </p>
    )
  }
  return (
    <form
      className="integration-automation"
      onSubmit={(e) => {
        e.preventDefault()
        if (name.trim()) void save()
      }}
    >
      <label>
        자동화 이름
        <input
          type="text"
          value={name}
          maxLength={100}
          placeholder="예: 월간 예산 집행 취합"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <button type="submit" className="mapping-confirm" disabled={busy || !name.trim()}>
        {busy ? '저장 중…' : '자동화로 저장'}
      </button>
      <p className="upload-drop-hint">
        승인한 열 매핑 규칙, 기관·지역 이름 연결, 검증 조건, 결과 열 순서를 저장합니다. 입력 파일은 저장하지 않습니다.
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

export default IntegrationResult
