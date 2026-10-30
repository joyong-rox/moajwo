import { useCallback, useEffect, useRef, useState } from 'react'
import {
  authStatus,
  fetchHealth,
  logout,
  UNAUTHORIZED_EVENT,
  type Health,
  type Me,
  type Review,
  type Role,
  type ValidationRequest,
} from './api'
import Account from './Account'
import { AuthContext, isManager, useAuth } from './authContext'
import Automations from './Automations'
import Bundle from './Bundle'
import Dashboard from './Dashboard'
import Dictionary from './Dictionary'
import FirstPasswordChange from './FirstPasswordChange'
import Login from './Login'
import Analysis from './Analysis'
import MappingCommon from './MappingCommon'
import MappingWorkspace from './MappingWorkspace'
import MoajwoAdmin from './MoajwoAdmin'
import Promotions from './Promotions'
import ReviewPanel from './ReviewPanel'
import HomeSteps from './HomeSteps'
import KeyJoin from './KeyJoin'
import { afterRouteChange, cancelFocusWaits, focusPageStart } from './pageFocus'
import { navigate, replaceSearch, usePath, useSearch } from './router'
import { ADMIN_BASE, findRoute, isActiveMenu, ROUTES } from './routes'
import Reviews from './Reviews'
import Runs from './Runs'
import Settings from './Settings'
import Upload from './Upload'
import Validation, { type ResultMode } from './Validation'
import { Banner, Icon, type IconName } from './Workflow'

type Gate =
  | { kind: 'loading' }
  | { kind: 'signed-out'; setupRequired: boolean; unavailable?: string; notice?: string }
  | { kind: 'signed-in'; me: Me }

async function resolveGate(): Promise<Gate> {
  try {
    // 로그인 여부와 최초 설정 필요 여부를 한 번에 (로그인 전에도 401 없이)
    const { setup_required, user } = await authStatus()
    if (user) return { kind: 'signed-in', me: user }
    return { kind: 'signed-out', setupRequired: setup_required }
  } catch (err) {
    // 상태를 확인하지 못했을 때(서버·DB 연결 실패 등) 최초 설정 화면을 열지 않는다 — 로그인 화면에 이유만 보인다
    return { kind: 'signed-out', setupRequired: false, unavailable: err instanceof Error ? err.message : String(err) }
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
  const signOut = () => {
    void logout().finally(() => {
      setGate({ kind: 'signed-out', setupRequired: false })
      navigate('/')
    })
  }
  if (gate.kind === 'signed-out') {
    return (
      <Login
        setupRequired={gate.setupRequired}
        unavailable={gate.unavailable}
        notice={gate.notice}
        onSignedIn={(me) => setGate({ kind: 'signed-in', me })}
      />
    )
  }
  if (gate.me.must_change_password) {
    // 임시 비밀번호 상태: 본인 비밀번호를 정하기 전에는 업무 화면을 열지 않는다 (서버도 업무 API를 403으로 막는다).
    // 바꾸면 서버가 세션을 끝내므로 로그인 화면으로 돌아가 새 비밀번호로 다시 로그인한다.
    return (
      <FirstPasswordChange
        me={gate.me}
        onDone={() => {
          setGate({ kind: 'signed-out', setupRequired: false, notice: '비밀번호를 바꿨습니다. 새 비밀번호로 다시 로그인하세요.' })
          navigate('/')
        }}
        onSignOut={signOut}
      />
    )
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
  const search = useSearch()
  const route = findRoute(path)
  const allowed = route !== undefined && route.roles.includes(me.role)
  // 주소가 바뀌어 화면이 그려진 뒤 한 번 초점을 정한다 (이동 의도가 있을 때만 옮기고, 없으면 사라진 초점만 되살림 — pageFocus.ts)
  const lastUrl = useRef(`${window.location.pathname}${window.location.search}`)
  useEffect(() => {
    const now = `${window.location.pathname}${window.location.search}`
    if (now === lastUrl.current) return
    const left = lastUrl.current
    lastUrl.current = now
    afterRouteChange(left)
  }, [path, search])
  useEffect(() => cancelFocusWaits, []) // 화면이 내려가면(로그아웃·계정 바꿈) 기다리던 초점 이동을 무효로

  const [state, setState] = useState<HealthState>({ kind: 'loading' })
  // 업로드·개인정보 확인 후 검증 섹션의 파일 목록을 다시 불러온다
  const [filesVersion, setFilesVersion] = useState(0)
  const refreshFiles = () => setFilesVersion((v) => v + 1)
  const [reviewRequest, setReviewRequest] = useState<ValidationRequest | null>(null)
  // 홈에서 연 검토 — 주소(/?review=번호)로 새로고침·직접 접근해도 같은 검토를 다시 연다
  const [homeReviewId, setHomeReviewId] = useState<number | null>(() => {
    if (window.location.pathname !== '/') return null
    const id = Number(new URLSearchParams(window.location.search).get('review'))
    return Number.isInteger(id) && id > 0 ? id : null
  })
  const [selected, setSelected] = useState<number[]>([])
  const [mode, setMode] = useState<ResultMode>('table') // 홈에서 고른 결과 형태 (단계 안내에 씀)
  const onHomeReview = useCallback((review: Review) => {
    if (window.location.pathname === '/') {
      const q = new URLSearchParams(window.location.search)
      q.set('review', String(review.id))
      replaceSearch(`?${q.toString()}`)
    }
  }, [])
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
      {/* 반복 메뉴를 건너뛰어 지금 화면의 제목(없으면 본문 시작)으로 — 숨긴 홈·다른 화면의 제목은 잡지 않는다 */}
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault() // 주소에 #main을 남기지 않는다 (화면 주소·검색 조건 그대로)
          focusPageStart()
        }}
      >
        본문으로 바로가기
      </a>
      <Sidebar path={path} />
      <div className="shell-main">
        <main id="main" className={`app${NARROW_PAGES.has(path) ? ' app-narrow' : ''}`}>
          {/* 홈(취합 작업)은 다른 메뉴로 옮겨도 진행 중인 검토가 사라지지 않도록 숨기기만 한다 */}
          <div id="home" hidden={path !== '/'}>
            {/* 홈 작업 영역: 넓은 화면을 쓰고 '올리기 → 고르기 → 검토 시작' 순서로 보인다 (대시보드는 아래) */}
            <div className="wk-home">
              <header className="wk-home-head">
                <div>
                  <h1 className="page-title">홈</h1>
                  <p className="wk-home-lead">
                    파일을 올리고 고른 뒤, 결과 형태(하나의 통합표 · 파일별 시트 · 공통 key 연결)를 골라 이어서 진행하세요.
                  </p>
                </div>
                <ServerStatus state={state} />
              </header>
              {state.kind === 'error' && (
                <Banner tone="block" icon="block" title="서버에 연결할 수 없습니다" live="alert">
                  <p>연결 실패: {state.message}</p>
                </Banner>
              )}
              {/* 검토를 열면 검토 화면의 단계 안내를 쓴다 (같은 안내를 두 번 보이지 않음) */}
              {!reviewRequest && homeReviewId === null && <HomeSteps refreshKey={filesVersion} selected={selected} mode={mode} />}

              <Upload onUploaded={refreshFiles} onPiiChanged={refreshFiles} />
              <Validation
                refreshKey={filesVersion}
                canManageGlossary={isManager(me.role)}
                onGlossaryChanged={() => undefined}
                onSelection={setSelected}
                onMode={setMode}
                onOpenReview={(request) => {
                  setHomeReviewId(null)
                  setReviewRequest(request)
                  setReviewKey((k) => k + 1)
                }}
              />
              {(reviewRequest || homeReviewId !== null) && (
                <ReviewPanel
                  key={reviewKey}
                  request={reviewRequest ?? undefined}
                  reviewId={reviewRequest ? undefined : (homeReviewId ?? undefined)}
                  onLoaded={onHomeReview}
                  onClose={() => {
                    setReviewRequest(null)
                    setHomeReviewId(null)
                    const q = new URLSearchParams(window.location.search)
                    q.delete('review')
                    replaceSearch(q.toString() ? `?${q.toString()}` : '')
                    refreshFiles()
                    setHomeVisit((v) => v + 1)
                  }}
                />
              )}
              <Dashboard refreshKey={homeVisit} />
            </div>
          </div>
          {path !== '/' && (
            <>
              {route === undefined && <NotFound />}
              {route !== undefined && !allowed && <Forbidden roles={route.roles} />}
              {allowed && !path.startsWith(ADMIN_BASE) && path !== '/users' && (
                <h1 className="page-title">{route.label}</h1>
              )}
              {allowed && path === '/automations' && <Automations />}
              {allowed && path === '/runs' && <Runs />}
              {allowed && path === '/reviews' && <Reviews />}
              {allowed && path === '/mapping' && <MappingWorkspace />}
              {allowed && path === '/mapping/common' && <MappingCommon />}
              {allowed && path === '/bundle' && <Bundle />}
              {allowed && path === '/keyjoin' && <KeyJoin />}
              {allowed && path === '/analysis' && <Analysis />}
              {allowed && path === '/dictionary' && <Dictionary />}
              {allowed && path === '/promotions' && <Promotions />}
              {allowed && path.startsWith(ADMIN_BASE) && <MoajwoAdmin path={path} />}
              {allowed && path === '/users' && <Redirect to={`${ADMIN_BASE}/users`} />}
              {allowed && path === '/settings' && <Settings />}
              {allowed && path === '/account' && <Account />}
            </>
          )}
        </main>
      </div>
    </div>
  )
}

/** 홈 머리 영역의 서버 상태 (예전 '백엔드 상태' 칸) — 색만이 아니라 글자로도 알린다 */
function ServerStatus({ state }: { state: HealthState }) {
  const ok = state.kind === 'ok' && state.health.status === 'ok' && state.health.database === 'ok'
  const look = state.kind === 'loading' ? 'loading' : ok ? 'ok' : 'fail'
  const text =
    state.kind === 'loading' ? '서버 확인 중…' : state.kind === 'error' ? '서버 연결 실패' : ok ? '서버 정상' : '서버 확인 필요'
  return (
    <p className={`wk-server is-${look}`} role="status" aria-live="polite">
      <span className="wk-server-dot" aria-hidden="true" />
      {text}
      {state.kind === 'ok' && (
        <span className="wk-server-detail">
          API {state.health.status} · DB {state.health.database}
        </span>
      )}
    </p>
  )
}

// 양식 위주 화면은 좁은 칸, 나머지(목록·작업 화면)는 넓은 칸을 쓴다
const NARROW_PAGES = new Set(['/settings', '/account'])

// 메뉴 아이콘 — 글자와 함께 보여 훑어보며 찾기 쉽게 (아이콘만으로 구분하지 않음)
const MENU_ICON: Record<string, IconName> = {
  '/': 'home',
  '/automations': 'bolt',
  '/runs': 'list',
  '/reviews': 'clipboard',
  '/mapping': 'table',
  '/analysis': 'chart',
  '/dictionary': 'book',
  '/promotions': 'arrowup',
  [ADMIN_BASE]: 'shield',
  '/settings': 'gear',
}

function MenuLink({ to, label, active, icon }: { to: string; label: string; active: boolean; icon?: IconName }) {
  return (
    <a
      href={to}
      className={`side-link${active ? ' is-active' : ''}`}
      aria-current={active ? 'page' : undefined}
      onClick={(e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return // 새 탭 열기는 브라우저에 맡김
        e.preventDefault()
        navigate(to, { focus: { to: 'page' } }) // 다른 화면으로: 새 화면의 제목으로 초점
      }}
    >
      {icon && <Icon name={icon} />}
      <span className="side-label">{label}</span>
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
      {/* 로그인한 계정과 역할을 맨 위에 뱃지로 보여준다. 일반 div에는 aria-label을 쓸 수 없어, 같은 설명을 보이지 않는 글자로 둔다
          (화면 읽기: '로그인 계정 이름, 역할 일반 사용자' — 짧은 뱃지 글자 대신 역할 전체 이름) */}
      <div className="side-account">
        <span className="sr-only">로그인 계정 </span>
        <span className="side-account-name">{me.display_name}</span>
        <span className="sr-only">, 역할 {me.role_label}</span>
        <span aria-hidden="true">
          <RoleBadge role={me.role} />
        </span>
      </div>
      <nav className="side-nav" aria-label="메뉴">
        {ROUTES.filter((r) => r.inMenu && r.roles.includes(me.role)).map((r) => (
          <MenuLink key={r.path} to={r.path} label={r.label} icon={MENU_ICON[r.path]} active={isActiveMenu(r.path, path)} />
        ))}
        {/* 데이터 연결은 아직 준비 중: 누를 수 없게 두고 뱃지만 단다 */}
        <button type="button" className="side-link is-disabled" disabled aria-disabled="true">
          <Icon name="link" />
          <span className="side-label">데이터 연결 (DB·Kibana)</span> <span className="side-badge">준비 중</span>
        </button>
      </nav>
      {/* 권한 없는 관리 메뉴는 그리지 않고, 그런 메뉴가 있다는 것만 알린다 */}
      {me.role !== 'owner' && (
        <p className="side-locked">
          <LockIcon />
          관리 메뉴는 최고 관리자(소유자)만 볼 수 있습니다.
        </p>
      )}
      <div className="side-user">
        <div className="side-user-actions">
          <MenuLink to="/account" label="내 계정" icon="user" active={path === '/account'} />
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

const ROLE_SHORT: Record<Role, string> = { user: '일반', owner: '최고 관리자' }

function RoleBadge({ role }: { role: Role }) {
  return <span className={`role-badge is-${role}`}>{ROLE_SHORT[role] ?? role}</span>
}

/** 예전 주소(/users)는 새 관리자 화면으로 바꿔 연다 (뒤로가기에 남지 않게 replace) */
function Redirect({ to }: { to: string }) {
  useEffect(() => {
    window.history.replaceState(null, '', to)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [to])
  return null
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
  const need = roles.includes('user') ? '로그인한 사용자' : '최고 관리자(소유자)'
  return (
    <section className="page-error" aria-label="권한 없음">
      <h1 className="page-title">
        <LockIcon />
        403 · 이 화면을 볼 권한이 없습니다
      </h1>
      <p>
        필요한 권한: <strong>{need}</strong> · 내 권한: <RoleBadge role={me.role} />
      </p>
      <p>필요하면 최고 관리자에게 문의하세요.</p>
      <MenuLink to="/" label="홈으로" active={false} />
    </section>
  )
}

export default App
