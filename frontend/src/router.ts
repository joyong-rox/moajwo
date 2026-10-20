import { useSyncExternalStore } from 'react'
import { markNavigation, setFocusIntent, type FocusIntent } from './pageFocus'

// 외부 라우터 라이브러리 없이 History API만 쓰는 작은 라우터.
// 운영(nginx)은 모르는 경로를 index.html로 돌려주므로(SPA fallback) 새로고침·직접 접근도 같은 화면이 열린다.

const NAVIGATE_EVENT = 'moajwo:navigate'

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange)
  window.addEventListener(NAVIGATE_EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(NAVIGATE_EVENT, onChange)
  }
}

/** 끝의 '/'를 뗀 현재 경로 ('/'는 그대로) */
export function currentPath(): string {
  const path = window.location.pathname.replace(/\/+$/, '')
  return path === '' ? '/' : path
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, currentPath, () => '/')
}

/** 주소의 검색 조건 ('?review=12' 등) — 바뀌면 다시 그린다 (뒤로가기 포함) */
export function useSearch(): string {
  return useSyncExternalStore(subscribe, () => window.location.search, () => '')
}

/** path는 '/reviews?review=12'처럼 검색 조건을 포함할 수 있다 (같은 주소면 기록을 만들지 않음).
 * 검색 조건 없는 경로는 예전처럼 경로만 비교한다 — 메뉴를 다시 눌러도 그 화면의 검색 조건(고른 파일 등)을 지우지 않는다.
 * clearSearch: 같은 경로라도 검색 조건을 지운 주소로 옮긴다 (상세에서 '목록으로'·'닫기'처럼 검색 조건을 버리는 이동).
 * focus: 옮긴 뒤 키보드 초점을 어디에 둘지 (pageFocus.ts). 없으면 초점을 옮기지 않는다. */
export function navigate(path: string, { clearSearch = false, focus }: { clearSearch?: boolean; focus?: FocusIntent } = {}) {
  const here = path.includes('?') || clearSearch ? `${currentPath()}${window.location.search}` : currentPath()
  if (path === here) return
  setFocusIntent(focus ?? null) // 실제로 옮길 때만 남긴다 (같은 주소면 위에서 끝남)
  markNavigation('push')
  window.history.pushState(null, '', path)
  window.dispatchEvent(new Event(NAVIGATE_EVENT))
  window.scrollTo(0, 0)
}

/** 화면 상태(지금 연 검토 등)를 주소에 남긴다 — 기록을 만들지 않아 뒤로가기가 쌓이지 않는다 */
export function replaceSearch(search: string) {
  const next = `${window.location.pathname}${search}`
  if (next === `${window.location.pathname}${window.location.search}`) return
  markNavigation('replace')
  window.history.replaceState(null, '', next)
  window.dispatchEvent(new Event(NAVIGATE_EVENT))
}
