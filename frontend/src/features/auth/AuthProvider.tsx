// The token is an httpOnly cookie the browser holds and JS cannot read — so
// "am I signed in" is answered by asking the server, not by inspecting local
// state. On boot we call /api/auth/me once.

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { api, AUTH_EXPIRED_EVENT } from '@/lib/api'
import type { Rep } from '@/lib/types'

import { AuthContext } from './authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [rep, setRep] = useState<Rep | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api
      .me()
      .then((r) => { if (alive) setRep(r) })
      .catch(() => { if (alive) setRep(null) }) // 401 on first load is normal
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  // A 401 mid-session (the cookie expired) drops us back to the login screen,
  // rather than leaving a shell where every action fails. api.ts fires this.
  useEffect(() => {
    const onExpired = () => setRep(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback(async (identifier: string, password: string) => {
    setRep(await api.login(identifier, password))
  }, [])

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined)
    setRep(null)
  }, [])

  const value = useMemo(
    () => ({ rep, loading, login, logout }),
    [rep, loading, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
