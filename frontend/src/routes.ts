import type { Role } from './api'

export type RouteDef = { path: string; label: string; roles: Role[]; inMenu: boolean }

const ALL: Role[] = ['user', 'admin', 'owner']

/** 화면 경로와 볼 수 있는 역할. 메뉴에는 볼 수 있는 것만 보인다 (서버도 같은 기준으로 403을 돌려준다). */
export const ROUTES: RouteDef[] = [
  { path: '/', label: '홈', roles: ALL, inMenu: true },
  { path: '/automations', label: '자동화', roles: ALL, inMenu: true },
  { path: '/runs', label: '실행 기록', roles: ALL, inMenu: true },
  { path: '/dictionary', label: '사전 관리', roles: ['admin', 'owner'], inMenu: true },
  { path: '/promotions', label: '승격 심사', roles: ['admin', 'owner'], inMenu: true },
  { path: '/users', label: '사용자 관리', roles: ['admin', 'owner'], inMenu: true },
  { path: '/settings', label: '설정', roles: ['owner'], inMenu: true },
  { path: '/account', label: '내 계정', roles: ALL, inMenu: false },
]

export function findRoute(path: string): RouteDef | undefined {
  return ROUTES.find((r) => r.path === path)
}
