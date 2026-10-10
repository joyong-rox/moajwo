import { useEffect, useState } from 'react'
import {
  authStatus,
  fetchHealth,
  logout,
  UNAUTHORIZED_EVENT,
  type Health,
  type Me,
  type Role,
  type ValidationRequest,
} from './api'
import Account from './Account'
import { AuthContext, isManager, useAuth } from './authContext'
import Automations from './Automations'
import Dashboard from './Dashboard'
import Dictionary from './Dictionary'
import Login from './Login'
import Promotions from './Promotions'
import ReviewPanel from './ReviewPanel'
import { navigate, usePath } from './router'
import { findRoute, ROUTES } from './routes'
import Runs from './Runs'
import Settings from './Settings'
import Upload from './Upload'
import Users from './Users'
import Validation from './Validation'

type Gate = { kind: 'loading' } | { kind: 'signed-out'; setupRequired: boolean } | { kind: 'signed-in'; me: Me }

async function resolveGate(): Promise<Gate> {
  try {
    // 로그인 여부와 최초 설정 필요 여부를 한 번에 (로그인 전에도 401 없이)
    const { setup_required, user } = await authStatus()
    if (user) return { kind: 'signed-in', me: user }
    return { kind: 'signed-out', setupRequired: setup_required }
  } catch {
    return { kind: 'signed-out', setupRequired: false }
  }
}

/** 로그인 확인 → 로그인 화면 또는 작업 화면 */
function App() {
  const [gate, setGate] = useState<Gate>({ kind: 'loading' })

  useEffect(() => {
    let alive = true
    const check = () => {
      void resolveGate().then((g) => alive && setGate(g))
    }
    check()
    // 다른 요청에서 로그인이 풀린 것(401)을 알게 되면 로그인 화면으로
    window.addEventListener(UNAUTHORIZED_EVENT, check)
    return () => {
      alive = false
      window.removeEventListener(UNAUTHORIZED_EVENT, check)
    }
  }, [])

  if (gate.kind === 'loading') return <p className="login-loading">확인 중…</p>
  if (gate.kind === 'signed-out') {
    return <Login setupRequired={gate.setupRequired} onSignedIn={(me) => setGate({ kind: 'signed-in', me })} />
  }
  const signOut = () => {
    void logout().finally(() => {
      setGate({ kind: 'signed-out', setupRequired: false })
      navigate('/')
    })
  }
  return (
    <AuthContext.Provider value={{ me: gate.me, signOut }}>
      {/* 다른 계정으로 바꾸면 이전 계정의 화면 상태를 남기지 않는다 */}
      <Workspace key={gate.me.id} />
    </AuthContext.Provider>
  )
}

type HealthState = { kind: 'loading' } | { kind: 'ok'; health: Health } | { kind: 'error'; message: string }

function Workspace() {
  const { me } = useAuth()
  const path = usePath()
  const route = findRoute(path)
  const allowed = route !== undefined && route.roles.includes(me.role)

  const [state, setState] = useState<HealthState>({ kind: 'loading' })
  // 업로드·개인정보 확인 후 검증 섹션의 파일 목록을 다시 불러온다
  const [filesVersion, setFilesVersion] = useState(0)
  const refreshFiles = () => setFilesVersion((v) => v + 1)
  const [reviewRequest, setReviewRequest] = useState<ValidationRequest | null>(null)
  // 검토를 새로 열 때마다 패널을 새로 만든다 (이전 검토 상태를 섞지 않음)
  const [reviewKey, setReviewKey] = useState(0)
  // 홈으로 돌아올 때마다 대시보드·파일 목록을 새로 불러온다
  const [homeVisit, setHomeVisit] = useState(0)
  const [lastPath, setLastPath] = useState(path)
  if (path !== lastPath) {
    setLastPath(path)
    if (path === '/') {
      setHomeVisit((v) => v + 1)
      setFilesVersion((v) => v + 1)
    }
  }

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

  useEffect(() => {
    document.title = route && allowed ? `${route.label} · Moajwo` : 'Moajwo'
  }, [route, allowed])

  return (
    <div className="shell">
      <Sidebar path={path} />
      <div className="shell-main">
        <main className="app">
          {/* 홈(취합 작업)은 다른 메뉴로 옮겨도 진행 중인 검토가 사라지지 않도록 숨기기만 한다 */}
          <div id="home" hidden={path !== '/'}>
            <h1 className="page-title">홈</h1>
            <Dashboard refreshKey={homeVisit} />

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
            <Validation
              refreshKey={filesVersion}
              canManageGlossary={isManager(me.role)}
              onGlossaryChanged={() => undefined}
              onOpenReview={(request) => {
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
                  setHomeVisit((v) => v + 1)
                }}
              />
            )}
          </div>
          {path !== '/' && (
            <>
              {route === undefined && <NotFound />}
              {route !== undefined && !allowed && <Forbidden roles={route.roles} />}
              {allowed && <h1 className="page-title">{route.label}</h1>}
              {allowed && path === '/automations' && <Automations />}
              {allowed && path === '/runs' && <Runs />}
              {allowed && path === '/dictionary' && <Dictionary />}
              {allowed && path === '/promotions' && <Promotions />}
              {allowed && path === '/users' && <Users />}
              {allowed && path === '/settings' && <Settings />}
              {allowed && path === '/account' && <Account />}
            </>
          )}
        </main>
      </div>
    </div>
  )
}

function MenuLink({ to, label, active }: { to: string; label: string; active: boolean }) {
  return (
    <a
      href={to}
      className={`side-link${active ? ' is-active' : ''}`}
      aria-current={active ? 'page' : undefined}
      onClick={(e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return // 새 탭 열기는 브라우저에 맡김
        e.preventDefault()
        navigate(to)
      }}
    >
      {label}
    </a>
  )
}

function Sidebar({ path }: { path: string }) {
  const { me, signOut } = useAuth()
  return (
    <aside className="shell-side">
      <div className="side-brand">
        <strong>Moajwo</strong>
        <span>문서취합 자동화 도구</span>
      </div>
      {/* 로그인한 계정과 역할을 맨 위에 뱃지로 보여준다 */}
      <div className="side-account" aria-label={`로그인: ${me.display_name} (${me.role_label})`}>
        <span className="side-account-name">{me.display_name}</span>
        <RoleBadge role={me.role} />
      </div>
      <nav className="side-nav" aria-label="메뉴">
        {ROUTES.filter((r) => r.inMenu && r.roles.includes(me.role)).map((r) => (
          <MenuLink key={r.path} to={r.path} label={r.label} active={path === r.path} />
        ))}
        {/* 데이터 연결은 아직 준비 중: 누를 수 없게 두고 뱃지만 단다 */}
        <button type="button" className="side-link is-disabled" disabled aria-disabled="true">
          데이터 연결 (DB·Kibana) <span className="side-badge">준비 중</span>
        </button>
      </nav>
      {/* 권한 없는 관리 메뉴는 그리지 않고, 그런 메뉴가 있다는 것만 알린다 */}
      {me.role !== 'owner' && (
        <p className="side-locked">
          <LockIcon />
          {me.role === 'user' ? '관리 메뉴는 관리자 권한이 있어야 보입니다.' : '일부 메뉴는 소유자 권한이 있어야 보입니다.'}
        </p>
      )}
      <div className="side-user">
        <div className="side-user-actions">
          <MenuLink to="/account" label="내 계정" active={path === '/account'} />
          <button type="button" className="side-logout" onClick={signOut}>
            로그아웃
          </button>
        </div>
      </div>
    </aside>
  )
}

function NotFound() {
  return (
    <section className="page-error" aria-label="페이지 없음">
      <h1 className="page-title">404 · 페이지를 찾을 수 없습니다</h1>
      <p>주소가 바뀌었거나 없는 화면입니다.</p>
      <MenuLink to="/" label="홈으로" active={false} />
    </section>
  )
}

const ROLE_SHORT: Record<Role, string> = { user: '일반', admin: '관리자', owner: '소유자' }

function RoleBadge({ role }: { role: Role }) {
  return <span className={`role-badge is-${role}`}>{ROLE_SHORT[role]}</span>
}

function LockIcon() {
  return (
    <svg className="lock-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

function Forbidden({ roles }: { roles: Role[] }) {
  const { me } = useAuth()
  const need = roles.includes('admin') ? '관리자 또는 소유자' : '소유자'
  return (
    <section className="page-error" aria-label="권한 없음">
      <h1 className="page-title">
        <LockIcon />
        403 · 이 화면을 볼 권한이 없습니다
      </h1>
      <p>
        필요한 권한: <strong>{need}</strong> · 내 권한: <RoleBadge role={me.role} />
      </p>
      <p>필요하면 관리자에게 권한을 요청하세요.</p>
      <MenuLink to="/" label="홈으로" active={false} />
    </section>
  )
}

export default App
