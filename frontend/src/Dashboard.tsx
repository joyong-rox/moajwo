import { useEffect, useState } from 'react'
import { fetchDashboard, type Dashboard as DashboardData } from './api'

function formatSaved(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}분`
  const hours = Math.floor(minutes / 60)
  return `${hours.toLocaleString('ko-KR')}시간 ${minutes % 60}분`
}

/** 홈 화면 맨 위 통계. refreshKey가 바뀌면(홈으로 돌아올 때) 다시 불러온다. */
function Dashboard({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchDashboard()
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
      })
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  return (
    <section className="dash" aria-label="대시보드">
      <div className="dash-head">
        <h2>대시보드</h2>
        {data && <span className="dash-scope">{data.scope === 'all' ? '전체 사용자 기준' : '내 기록 기준'}</span>}
      </div>
      {error && (
        <p className="upload-error" role="alert">
          통계를 불러오지 못했습니다: {error}
        </p>
      )}
      {!data && !error && <p className="upload-drop-hint">불러오는 중…</p>}
      {data && (
        <dl className="dash-cards">
          <div className="dash-card">
            <dt>이번 달 실행 횟수</dt>
            <dd className="dash-value">{data.month_runs.toLocaleString('ko-KR')}회</dd>
            <dd className="dash-sub">
              {data.month} · 완료 {data.month_done}회
            </dd>
          </div>
          <div className="dash-card">
            <dt>누적 절약 시간</dt>
            <dd className="dash-value">{data.baseline_minutes === null && data.saved_runs === 0 ? '-' : formatSaved(data.saved_seconds)}</dd>
            <dd className="dash-sub">
              {data.baseline_minutes === null
                ? '소유자가 설정에서 수동 기준 소요 시간을 정하면 계산합니다'
                : `완료 ${data.saved_runs}건 · 기준 ${data.baseline_minutes}분 − 실제 처리 시간`}
              {data.runs_without_baseline > 0 && data.baseline_minutes !== null && ` · 기준 없음 ${data.runs_without_baseline}건 제외`}
            </dd>
          </div>
          <div className="dash-card">
            <dt>검증한 행 수</dt>
            <dd className="dash-value">{data.validated_rows.toLocaleString('ko-KR')}행</dd>
            <dd className="dash-sub">파일 {data.validated_files}개 (같은 파일은 한 번만)</dd>
          </div>
          <div className="dash-card">
            <dt>통합 전 발견한 오류</dt>
            <dd className="dash-value">{data.errors_found.toLocaleString('ko-KR')}건</dd>
            <dd className="dash-sub">
              차단 {data.blocks_found} · 경고 {data.warnings_found}
            </dd>
          </div>
        </dl>
      )}
    </section>
  )
}

export default Dashboard
