import { useSyncExternalStore } from 'react'

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

export function navigate(path: string) {
  if (path === currentPath()) return
  window.history.pushState(null, '', path)
  window.dispatchEvent(new Event(NAVIGATE_EVENT))
  window.scrollTo(0, 0)
}
