import { useEffect, useState } from 'react'
import { fetchHealth, type Health } from './api'

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; health: Health }
  | { kind: 'error'; message: string }

function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetchHealth(controller.signal)
      .then((health) => setState({ kind: 'ok', health }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setState({ kind: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => controller.abort()
  }, [])

  return (
    <main className="app">
      <h1>Moajwo</h1>
      <p className="subtitle">문서취합 자동화 도구</p>

      <section className="status" aria-live="polite">
        <h2>백엔드 상태</h2>
        {state.kind === 'loading' && <p>확인 중…</p>}
        {state.kind === 'ok' && (
          <dl>
            <dt>API</dt>
            <dd className={state.health.status === 'ok' ? 'ok' : 'fail'}>{state.health.status}</dd>
            <dt>DB</dt>
            <dd className={state.health.database === 'ok' ? 'ok' : 'fail'}>{state.health.database}</dd>
          </dl>
        )}
        {state.kind === 'error' && <p className="fail">연결 실패: {state.message}</p>}
      </section>
    </main>
  )
}

export default App
