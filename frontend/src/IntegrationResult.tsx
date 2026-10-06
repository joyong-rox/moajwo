import { useEffect, useState } from 'react'
import { integrateReview, listRuns, runDownloadUrl, type IntegrationRun } from './api'

const CHANGE_LABEL: Record<string, string> = {
  ffill: '병합 셀 채움',
  number: '숫자 변환',
  trim: '공백 정리',
  entity: '이름 정규화',
  column: '열 이름 매핑',
  other: '기타 변환',
}

/** 검토가 끝난 세션으로 통합 결과 엑셀을 만들고, 만든 결과 목록을 보여준다. */
function IntegrationResult({ reviewId }: { reviewId: number }) {
  const [runs, setRuns] = useState<IntegrationRun[]>([])
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
      const created = await integrateReview(reviewId)
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
  return (
    <div className="integration">
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
          </p>
          <p className="upload-drop-hint">
            {Object.entries(latest.summary.change_counts ?? {})
              .filter(([kind]) => kind !== 'column')
              .map(([kind, count]) => `${CHANGE_LABEL[kind] ?? kind} ${count}`)
              .join(' · ') || '셀 변경 없음'}
          </p>
        </div>
      )}
      {runs.length > 1 && (
        <details className="integration-history">
          <summary>이 검토로 만든 결과 {runs.length}개</summary>
          <ul>
            {runs.map((r) => (
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

export default IntegrationResult
