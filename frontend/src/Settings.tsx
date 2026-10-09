import { useEffect, useState } from 'react'
import { fetchSettings, saveAutomationBaseline, saveBaseline, type Settings as SettingsData } from './api'
import LlmSettings from './LlmSettings'
import { formatTime } from './runFormat'

function parseMinutes(text: string): number | null | undefined {
  const t = text.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) && n > 0 && n <= 10080 ? n : undefined
}

/** 설정 (소유자): 절약 시간 계산에 쓰는 수동 기준 소요 시간 */
function Settings() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [baseline, setBaseline] = useState('')
  const [perAutomation, setPerAutomation] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  function apply(s: SettingsData) {
    setData(s)
    setBaseline(s.manual_baseline_minutes?.toString() ?? '')
    setPerAutomation(Object.fromEntries(s.automations.map((a) => [a.id, a.manual_baseline_minutes?.toString() ?? ''])))
  }

  useEffect(() => {
    fetchSettings()
      .then(apply)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  async function save(action: () => Promise<SettingsData>, done: string) {
    setError(null)
    setMessage(null)
    try {
      apply(await action())
      setMessage(done)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="admin" aria-label="설정">
      <h2>설정</h2>
      <h3>수동 기준 소요 시간</h3>
      <p className="upload-drop-hint">
        실행 1건(파일을 모아 검토하고 통합하기까지)을 사람이 직접 할 때 걸리는 시간입니다. 대시보드의 누적 절약 시간 =
        완료된 실행마다 (기준 시간 − 실제 처리 시간)의 합 (0보다 작으면 0). 자동화별로 다르게 정하면 그 값을 씁니다.
      </p>
      {data && (
        <>
          <form
            className="admin-form"
            onSubmit={(e) => {
              e.preventDefault()
              const minutes = parseMinutes(baseline)
              if (minutes === undefined) {
                setError('0보다 크고 10080(7일) 이하인 분 단위 숫자를 넣으세요. 비우면 계산하지 않습니다.')
                return
              }
              void save(() => saveBaseline(minutes), '기본 기준 시간을 저장했습니다.')
            }}
          >
            <label>
              기본값 (분)
              <input
                inputMode="decimal"
                value={baseline}
                placeholder="예: 120"
                onChange={(e) => setBaseline(e.target.value)}
              />
            </label>
            <button type="submit" className="mapping-run">
              저장
            </button>
            <p className="upload-drop-hint">
              {data.updated_at ? `마지막 변경: ${data.updated_by_name ?? '-'} · ${formatTime(data.updated_at)}` : '아직 정하지 않았습니다.'}
            </p>
          </form>
          {data.automations.length > 0 && (
            <div className="upload-table-wrap">
              <table className="upload-table">
                <thead>
                  <tr>
                    <th scope="col">자동화</th>
                    <th scope="col">저장한 사람</th>
                    <th scope="col">기준 시간 (분, 비우면 기본값)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.automations.map((a) => (
                    <tr key={a.id}>
                      <th scope="row">{a.name}</th>
                      <td>{a.owner_name ?? '-'}</td>
                      <td>
                        <div className="admin-actions">
                          <input
                            inputMode="decimal"
                            aria-label={`${a.name} 기준 시간`}
                            value={perAutomation[a.id] ?? ''}
                            onChange={(e) => setPerAutomation((prev) => ({ ...prev, [a.id]: e.target.value }))}
                          />
                          <button
                            type="button"
                            className="mapping-confirm"
                            onClick={() => {
                              const minutes = parseMinutes(perAutomation[a.id] ?? '')
                              if (minutes === undefined) {
                                setError('0보다 크고 10080 이하인 숫자를 넣거나 비우세요.')
                                return
                              }
                              void save(() => saveAutomationBaseline(a.id, minutes), `'${a.name}' 기준 시간을 저장했습니다.`)
                            }}
                          >
                            저장
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {message && (
        <p className="mapping-message" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

/** 설정 화면: 수동 기준 소요 시간 + LLM 제공자 */
function SettingsPage() {
  return (
    <>
      <Settings />
      <LlmSettings />
    </>
  )
}

export default SettingsPage
