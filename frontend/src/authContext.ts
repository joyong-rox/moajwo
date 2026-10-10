import { createContext, useContext } from 'react'
import type { Me, Role } from './api'

export type AuthValue = { me: Me; signOut: () => void }

export const AuthContext = createContext<AuthValue | null>(null)

/** 로그인한 사용자 (로그인 화면 밖에서만 쓴다) */
export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (value === null) throw new Error('AuthContext 밖에서 useAuth를 썼습니다.')
  return value
}

export const MANAGERS: Role[] = ['admin', 'owner']

export const isManager = (role: Role) => MANAGERS.includes(role)
