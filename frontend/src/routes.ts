import type { Role } from './api'

export type RouteDef = {
  path: string
  label: string
  roles: Role[]
  inMenu: boolean
}

const ALL: Role[] = ['user', 'owner']
const OWNER: Role[] = ['owner']

export const ADMIN_BASE = '/moajwoAdmin'

/** 화면 경로와 볼 수 있는 역할. 메뉴에는 볼 수 있는 것만 보인다 (서버도 같은 기준으로 403을 돌려준다). */
export const ROUTES: RouteDef[] = [
  { path: '/', label: '홈', roles: ALL, inMenu: true },
  { path: '/automations', label: '자동화', roles: ALL, inMenu: true },
  { path: '/runs', label: '실행 기록', roles: ALL, inMenu: true },
  { path: '/mapping', label: '열 매핑', roles: ALL, inMenu: true },
  { path: '/mapping/common', label: '공통 항목 검토', roles: ALL, inMenu: false },
  { path: '/analysis', label: '데이터 분석', roles: ALL, inMenu: true },
  { path: '/dictionary', label: '사전 관리', roles: OWNER, inMenu: true },
  { path: '/promotions', label: '승격 심사', roles: OWNER, inMenu: true },
  { path: ADMIN_BASE, label: '관리자', roles: OWNER, inMenu: true },
  {
    path: `${ADMIN_BASE}/users`,
    label: '사용자·가입 신청',
    roles: OWNER,
    inMenu: false,
  },
  {
    path: `${ADMIN_BASE}/audit`,
    label: '관리 이력',
    roles: OWNER,
    inMenu: false,
  },
  { path: '/settings', label: '설정', roles: OWNER, inMenu: true },
  { path: '/users', label: '사용자 관리', roles: OWNER, inMenu: false }, // 예전 주소 → 관리자 화면으로 이동
  { path: '/account', label: '내 계정', roles: ALL, inMenu: false },
]

export function findRoute(path: string): RouteDef | undefined {
  return ROUTES.find((r) => r.path === path)
}

/** 메뉴 강조: 관리자 하위 화면에서도 '관리자' 메뉴를 강조한다 */
export function isActiveMenu(menuPath: string, path: string): boolean {
  if (menuPath === ADMIN_BASE || menuPath === '/mapping') return path === menuPath || path.startsWith(`${menuPath}/`)
  return menuPath === path
}
