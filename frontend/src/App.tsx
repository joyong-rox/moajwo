import { useEffect, useState } from 'react'
import { fetchHealth, type Health, type ValidationRequest } from './api'
import Upload from './Upload'
import Glossary from './Glossary'
import ReviewPanel from './ReviewPanel'
import Validation from './Validation'

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; health: Health }
  | { kind: 'error'; message: string }

function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  // 업로드·개인정보 확인 후 검증 섹션의 파일 목록을 다시 불러온다
  const [filesVersion, setFilesVersion] = useState(0)
  const refreshFiles = () => setFilesVersion((v) => v + 1)
  const [glossaryVersion, setGlossaryVersion] = useState(0)
  const refreshGlossary = () => setGlossaryVersion((v) => v + 1)
  const [reviewRequest, setReviewRequest] = useState<ValidationRequest | null>(null)
  // 검토를 새로 열 때마다 패널을 새로 만든다 (이전 검토 상태를 섞지 않음)
  const [reviewKey, setReviewKey] = useState(0)

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

      <Upload onUploaded={refreshFiles} onPiiChanged={refreshFiles} />
      <Validation refreshKey={filesVersion} onGlossaryChanged={refreshGlossary} onOpenReview={(request) => {
          setReviewRequest(request)
          setReviewKey((k) => k + 1)
        }}
      />
      {reviewRequest && (
        <ReviewPanel
          key={reviewKey}
          request={reviewRequest}
          onClose={() => {
            setReviewRequest(null)
            refreshFiles()
          }}
        />
      )}
      <Glossary refreshKey={glossaryVersion} onChanged={refreshGlossary} />
    </main>
  )
}

export default App
