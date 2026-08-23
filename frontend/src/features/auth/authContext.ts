// Context and hook, kept out of the provider's .tsx: a module that exports
// both a component and a non-component breaks react-refresh.

import { createContext, useContext } from 'react'

import type { Rep } from '@/lib/types'

export interface AuthState {
  rep: Rep | null
  loading: boolean
  login: (identifier: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}
