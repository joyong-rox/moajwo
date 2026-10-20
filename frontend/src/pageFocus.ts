// 화면 이동 뒤 키보드 초점.
// 주소가 바뀌었다고 매번 옮기지 않는다. 이동을 일으킨 쪽이 의도를 남기면(메뉴·다른 화면 링크·목록↔상세) 화면이 그려진 뒤 한 번 처리하고,
// 의도가 없으면(검색·필터·선택처럼 주소만 고친 경우, 백그라운드 갱신) 지금 초점을 그대로 둔다.
// 뒤로가기·앞으로가기처럼 의도 없이 화면이 바뀌어 초점이 사라졌을 때만(본문·숨긴 영역·떼어진 요소) 되살린다:
// 상세 주소면 상세 시작점(일반 열기와 같음), 상세를 연 버튼을 알면 그 버튼, 아니면 화면 시작점.
// 주소만 고친 경우(replaceSearch — 묶기에서 파일 빼기 등)는 초점이 사라져도 옮기지 않는다: 브라우저가 지운 요소 자리를
// 다음 Tab의 시작점으로 기억하므로, 화면 제목으로 옮기면 오히려 멀어진다.
// 확인창이 열려 있으면 아무것도 하지 않는다 (확인창이 초점을 가둔다).
// 상세가 그려지기를 기다리는 동안 새 이동(push·뒤로/앞으로)이나 화면 해제(로그아웃)가 있으면 그 대기는 무효가 되어 아무것도 하지 않는다.

export type FocusIntent =
  | { to: 'page' } // 새 화면의 제목 (없으면 본문 시작)
  | { to: 'detail'; returnKey?: string } // 상세의 시작점 (DETAIL_STARTS, 그려질 때까지 기다림). returnKey: 목록으로 돌아올 때 초점을 줄 '연 버튼'
  | { to: 'return' } // 목록으로 돌아옴: 연 버튼 → 없으면 목록(화면) 시작

// 상세 주소 → 상세의 시작점. 열지 못하면 그 자리의 오류 안내(role=alert)가 시작점이다 (일반 열기·앞으로가기 모두 같은 표를 쓴다)
const DETAIL_STARTS: { match: (u: URL) => boolean; selector: string }[] = [
  { match: (u) => u.pathname === '/reviews' && u.searchParams.has('review'), selector: 'section.review-panel h2' },
  { match: (u) => u.pathname === '/analysis' && u.searchParams.has('id'), selector: 'section.an h2, section.an [role="alert"]' },
]

let pending: FocusIntent | null = null
let source: 'push' | 'replace' | 'pop' = 'push' // 마지막 주소 변경의 원인 (router.ts가 알려 주고, 뒤로·앞으로는 popstate)
let generation = 0 // 새 이동(push·pop)·화면 해제마다 늘린다 — 이전 이동이 남긴 대기는 이 값이 바뀌면 그만둔다
const openers = new Map<string, string>() // 상세를 연 주소 → 그 주소로 돌아올 때 초점을 줄 요소의 data-focus-key

window.addEventListener('popstate', () => {
  source = 'pop'
})

export function markNavigation(kind: 'push' | 'replace') {
  source = kind
}

export function setFocusIntent(intent: FocusIntent | null) {
  pending = intent
}

/** 화면이 내려갈 때(로그아웃 등) 부른다: 기다리던 초점 이동을 모두 무효로 */
export function cancelFocusWaits() {
  generation++
  pending = null
}

/** 본문 링크용: 다른 화면(경로)으로 가면 새 화면 제목으로, 같은 화면에서 검색 조건만 바뀌면 초점을 옮기지 않는다 */
export function pageIntentFor(href: string): FocusIntent | undefined {
  return new URL(href, window.location.href).pathname !== window.location.pathname ? { to: 'page' } : undefined
}

function visible(el: Element | null): HTMLElement | null {
  return el instanceof HTMLElement && !el.closest('[hidden]') && el.getClientRects().length > 0 ? el : null
}

/** 활성 화면의 시작점: 본문(main) 안에서 숨기지 않은 첫 h1, 없으면 본문 자체 (숨긴 홈·다른 화면의 제목은 잡지 않음) */
export function pageStart(): HTMLElement | null {
  const main = document.querySelector<HTMLElement>('main.app')
  if (!main) return null
  return [...main.querySelectorAll('h1')].map(visible).find((h) => h !== null) ?? main
}

function focusEl(el: HTMLElement) {
  // 원래 초점을 받는 요소(링크·버튼·summary·tabindex가 있는 요소 등, tabIndex ≥ 0)는 그대로 둔다 — Tab 순서와 기존 tabindex를 바꾸지 않는다.
  // 제목·본문·오류 안내처럼 초점을 받지 않는 대상에만 tabindex="-1"을 잠시 붙이고, 초점이 떠나면 지운다
  if (el.tabIndex < 0 && !el.hasAttribute('tabindex')) {
    el.setAttribute('tabindex', '-1')
    el.addEventListener('blur', () => el.removeAttribute('tabindex'), { once: true })
  }
  el.focus()
}

export function focusPageStart() {
  const el = pageStart()
  if (el) focusEl(el)
}

function modalOpen() {
  return document.querySelector('[role="alertdialog"], [aria-modal="true"], dialog[open]') !== null
}

function focusLost() {
  const a = document.activeElement
  return !a || a === document.body || a === document.documentElement || !a.isConnected || a.closest('[hidden]') !== null
}

function byKey(key: string) {
  return visible(document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`))
}

function detailSelector(url: string): string | null {
  const u = new URL(url, window.location.href)
  return DETAIL_STARTS.find((d) => d.match(u))?.selector ?? null
}

/** 대상이 그려질 때까지(최대 timeout) 기다렸다가 초점을 준다. 시간 초과면 화면 시작점.
 * 기다리는 동안 새 이동·화면 해제가 있었거나(세대가 바뀜), 사용자가 초점을 다른 곳으로 옮겼거나, 확인창이 열리면 아무것도 하지 않고 끝낸다. */
function focusWhenReady(find: () => HTMLElement | null, timeout: number) {
  const mine = generation
  const started = performance.now()
  const was = document.activeElement
  const tick = () => {
    if (mine !== generation) return
    if (document.activeElement !== was && !focusLost()) return
    if (modalOpen()) return
    const el = find()
    if (el) return focusEl(el)
    if (performance.now() - started > timeout) return focusPageStart()
    requestAnimationFrame(tick)
  }
  tick()
}

/** 주소가 바뀌어 화면이 그려진 뒤 App이 한 번 부른다. left: 떠난 주소(경로+검색 조건) */
export function afterRouteChange(left: string) {
  const intent = pending
  const from = source
  pending = null
  source = 'push'
  if (from !== 'replace') generation++ // 새 이동: 이전 이동이 남긴 대기는 무효 (주소만 고친 경우는 이어서 기다린다)
  if (modalOpen()) return
  const here = `${window.location.pathname}${window.location.search}`
  if (intent?.to === 'page') return focusPageStart()
  const detail = detailSelector(here)
  if (intent?.to === 'detail') {
    if (intent.returnKey) openers.set(left, intent.returnKey)
    if (!detail) return focusPageStart()
    return focusWhenReady(() => visible(document.querySelector(detail)), 3000)
  }
  const key = openers.get(here)
  if (intent?.to === 'return') return key ? focusWhenReady(() => byKey(key), 3000) : focusPageStart()
  // 의도 없음: 초점이 그대로 살아 있으면 두고, 사라졌을 때만 (뒤로·앞으로) 상세 시작점 → 연 버튼 → 화면 시작
  if (from === 'replace' || !focusLost()) return
  if (detail) focusWhenReady(() => visible(document.querySelector(detail)), 3000)
  else if (key) focusWhenReady(() => byKey(key), 3000)
  else focusPageStart()
}
