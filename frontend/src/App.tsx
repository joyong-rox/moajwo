import { useEffect, useState } from 'react'
import { fetchHealth, type Health, type ValidationRequest } from './api'
import Upload from './Upload'
import Automations from './Automations'
import Runs from './Runs'
import Glossary from './Glossary'
import ReviewPanel from './ReviewPanel'
import Validation from './Validation'

type Tab = 'work' | 'automation' | 'runs'
const TABS: { id: Tab; label: string }[] = [
  { id: 'work', label: '취합 작업' },
  { id: 'automation', label: '자동화' },
  { id: 'runs', label: '실행 기록' },
]

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
  const [tab, setTab] = useState<Tab>('work')

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

      <nav className="app-tabs" role="tablist" aria-label="메뉴">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className={`app-tab${tab === t.id ? ' is-active' : ''}`}
            onClick={() => {
              // 다른 메뉴에서 올린 파일이 검증 목록에 보이도록 돌아올 때 다시 불러온다
              if (t.id === 'work' && tab !== 'work') refreshFiles()
              setTab(t.id)
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* 취합 작업은 메뉴를 옮겨도 진행 중인 검토가 사라지지 않도록 숨기기만 한다 */}
      <div role="tabpanel" id="panel-work" aria-labelledby="tab-work" hidden={tab !== 'work'}>
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
      </div>
      {tab === 'automation' && (
        <div role="tabpanel" id="panel-automation" aria-labelledby="tab-automation">
          <Automations />
        </div>
      )}
      {tab === 'runs' && (
        <div role="tabpanel" id="panel-runs" aria-labelledby="tab-runs">
          <Runs />
        </div>
      )}
    </main>
  )
}

export default App
