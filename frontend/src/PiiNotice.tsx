import { useState } from 'react'
import { acknowledgePii, PII_LABEL, type PiiSummary, type PiiType } from './api'

type Props = {
  fileId: number
  pii: PiiSummary
  onAcknowledged?: (pii: PiiSummary) => void
}

/** 개인정보 의심 패턴 경고. 원문이 아닌 마스킹 값과 위치만 보여주고, 사용자 확인을 받는다. */
function PiiNotice({ fileId, pii, onAcknowledged }: Props) {
  const [state, setState] = useState(pii)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const summary = (Object.keys(PII_LABEL) as PiiType[])
    .filter((t) => state.counts[t] > 0)
    .map((t) => `${PII_LABEL[t]} ${state.counts[t]}건`)
    .join(' · ')

  async function acknowledge() {
    setBusy(true)
    setError(null)
    try {
      const next = await acknowledgePii(fileId)
      setState(next)
      onAcknowledged?.(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`pii-notice${state.acknowledged ? ' is-acknowledged' : ''}`} role="alert">
      <p className="pii-title">
        {state.acknowledged ? '개인정보 포함 확인됨' : '개인정보 의심 패턴 발견 — 통합 전 확인 필요'}: {summary}
      </p>
      <details>
        <summary>위치 보기 (마스킹)</summary>
        <ul className="pii-items">
          {state.items.map((item, i) => (
            <li key={i}>
              {item.sheet} {item.column}
              {item.row} · {PII_LABEL[item.type]} · <code>{item.masked}</code>
            </li>
          ))}
        </ul>
      </details>
      {!state.acknowledged && (
        <button type="button" className="mapping-confirm" disabled={busy} onClick={() => void acknowledge()}>
          개인정보 포함을 확인했습니다
        </button>
      )}
      {error && <span className="upload-error">{error}</span>}
    </div>
  )
}

export default PiiNotice
