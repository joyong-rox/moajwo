import { useEffect, useRef, useState } from 'react'
import type { Stage } from './progressStages'

const UPLOAD_SHARE = 35 // 실제 업로드 바이트 진행률이 차지하는 막대 비율(%)
const CAP = 95 // 서버 응답 전에는 95%를 넘기지 않는다

/**
 * 진행 상태 막대. 서버는 한 번의 요청으로 처리하므로 단계 문구와 막대는 예상 시간에 맞춘 추정치다.
 * 화면에 있는 동안만 시간이 흐른다 (끝나면 부모가 내린다).
 * uploadFraction: 0~1 실제 업로드 진행률 (주면 첫 단계는 실제 바이트 기준)
 */
export function ProgressPanel({
  stages,
  uploadFraction,
  title,
}: {
  stages: Stage[]
  uploadFraction?: number
  title?: string
}) {
  const uploading = uploadFraction !== undefined && uploadFraction < 1
  const startRef = useRef<number | null>(null)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (uploading) return // 업로드가 끝난 뒤부터 처리 단계 시간을 잰다
      if (startRef.current === null) startRef.current = performance.now()
      setElapsed(performance.now() - startRef.current)
    }, 200)
    return () => window.clearInterval(timer)
  }, [uploading])

  const rest = uploadFraction !== undefined ? stages.slice(1) : stages
  const total = rest.reduce((sum, s) => sum + s.ms, 0)
  let index = 0
  let acc = 0
  for (let i = 0; i < rest.length; i++) {
    acc += rest[i].ms
    index = i
    if (elapsed < acc) break
  }
  const base = uploadFraction !== undefined ? UPLOAD_SHARE : 0
  // 예상 시간의 두 배에 다가갈수록 천천히 95%에 가까워진다 (늦어져도 멈춰 보이지 않게)
  const eased = (CAP - base) * (1 - Math.exp((-2 * elapsed) / Math.max(total, 1)))
  const percent = uploading ? Math.round((uploadFraction ?? 0) * UPLOAD_SHARE) : Math.round(base + eased)
  const label = uploading ? `${stages[0].label} ${Math.round((uploadFraction ?? 0) * 100)}%` : rest[index].label
  const current = uploading ? 0 : index + (uploadFraction !== undefined ? 1 : 0)

  return (
    <div className="progress" role="status" aria-live="polite">
      {title && <p className="progress-title">{title}</p>}
      <div
        className="progress-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label={label}
      >
        <span className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <p className="progress-label">
        <span>{label}</span>
        <span className="progress-percent">{percent}%</span>
      </p>
      <ol className="progress-steps">
        {stages.map((s, i) => (
          <li key={s.label} className={i < current ? 'is-done' : i === current ? 'is-active' : undefined}>
            {s.label.replace(/….*$/, '')}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** 처리 중인 파일 자리에 보여주는 뼈대(스켈레톤) */
export function SkeletonRows({ names }: { names: string[] }) {
  return (
    <ul className="skeleton-list" aria-hidden="true">
      {names.map((name) => (
        <li key={name} className="skeleton-card">
          <span className="skeleton-name">{name}</span>
          <span className="skeleton-line" />
          <span className="skeleton-line is-short" />
        </li>
      ))}
    </ul>
  )
}
