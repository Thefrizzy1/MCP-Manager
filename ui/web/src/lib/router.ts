import { useSyncExternalStore } from 'react'

/** Minimal hash router (#/route). Robust under FastAPI's /app SPA serving —
 *  no server route config, no basename juggling. */
function currentRoute(): string {
  const h = window.location.hash.replace(/^#\/?/, '')
  return h.split('?')[0] || 'dashboard'
}

export function navigate(route: string): void {
  window.location.hash = '#/' + route.replace(/^#?\/?/, '')
}

export function useRoute(): string {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('hashchange', cb)
      return () => window.removeEventListener('hashchange', cb)
    },
    currentRoute,
    () => 'dashboard',
  )
}
