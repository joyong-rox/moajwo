import { useCallback, useEffect, useState } from 'react'
import { listAllRuns, runDownloadUrl, type IntegrationRun } from './api'
import ReviewPanel from './ReviewPanel'
import { formatDuration, formatTime, STATUS_CLASS } from './runFormat'

/** 실행 기록: runs 테이블 기반 (입력 파일 수, 결과 파일 경로, 상태, 소요 시간). */
function Runs() {
  const [runs, setRuns] = useState<IntegrationRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openReview, setOpenReview] = useState<number | null>(null)

  const load = useCallback(() => {
    listAllRuns()
      .then((list) => {
        setRuns(list)
        setError(null)
      })
      .catch((err: unknown) => setError(`실행 기록을 불러오지 못했습니다: ${err instanceof Error ? err.message : err}`))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <section className="runs" aria-label="실행 기록">
      <div className="runs-head">
        <h2>실행 기록</h2>
        <button type="button" className="mapping-confirm" onClick={load}>
          새로고침
        </button>
      </div>
      <p className="upload-drop-hint">
        통합 결과를 만든 실행과 자동화 실행을 최신 순으로 보여줍니다. 상태: 완료 · 검토(확인할 항목이 남아 대기) · 실패.
      </p>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {runs !== null && runs.length === 0 && <p className="upload-drop-hint">아직 실행 기록이 없습니다.</p>}
      {runs !== null && runs.length > 0 && (
        <div className="runs-scroll">
          <table className="runs-table">
            <thead>
              <tr>
                <th scope="col">번호</th>
                <th scope="col">시작</th>
                <th scope="col">방식</th>
                <th scope="col">입력 파일</th>
                <th scope="col">상태</th>
                <th scope="col">소요 시간</th>
                <th scope="col">결과 파일</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td data-label="번호">#{r.id}</td>
                  <td data-label="시작">{formatTime(r.started_at)}</td>
                  <td data-label="방식">
                    {r.trigger === 'automation'
                      ? `자동화 ${r.automation_name ? `'${r.automation_name}'` : '(삭제됨)'}`
                      : '직접 검토'}
                  </td>
                  <td data-label="입력 파일">{r.input_file_count}개</td>
                  <td data-label="상태">
                    <span className={`runs-status ${STATUS_CLASS[r.status] ?? ''}`}>{r.status_label}</span>
                  </td>
                  <td data-label="소요 시간">{formatDuration(r.duration_ms)}</td>
                  <td className="runs-path" data-label="결과 파일">
                    {r.status === 'done' && r.output_path ? (
                      <>
                        <code>{r.output_path}</code> <a href={runDownloadUrl(r.id)}>내려받기</a>
                      </>
                    ) : r.status === 'review' && r.review_session_id !== null ? (
                      <button
                        type="button"
                        className="mapping-confirm"
                        onClick={() => setOpenReview(r.review_session_id)}
                      >
                        검토 이어서 하기
                      </button>
                    ) : r.status === 'failed' ? (
                      <span className="upload-error">{r.error ?? '실패'}</span>
                    ) : (
                      '-'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {openReview !== null && (
        <ReviewPanel
          key={openReview}
          reviewId={openReview}
          onClose={() => {
            setOpenReview(null)
            load()
          }}
        />
      )}
    </section>
  )
}

export default Runs
