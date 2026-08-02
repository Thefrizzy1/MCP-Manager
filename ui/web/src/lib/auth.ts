import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface WhoAmI {
  username: string
  role: 'admin' | 'user'
  must_change: boolean
  default_password_active: boolean
}

export interface UiUser {
  username: string
  role: 'admin' | 'user'
  must_change: boolean
  is_default: boolean
  created: string
}

/** The signed-in user. Cached briefly; the 401 handler in api.ts bounces to
 *  /login if the session is gone, so this never renders stale-authed. */
export function useWhoami() {
  return useQuery({
    queryKey: ['whoami'],
    queryFn: () => api.get<WhoAmI>('/api/v1/auth/whoami'),
    staleTime: 60_000,
  })
}

export async function logout(): Promise<void> {
  try {
    await api.post('/api/v1/auth/logout')
  } catch {
    /* clearing the cookie is best-effort; navigate regardless */
  }
  window.location.assign('/login')
}
