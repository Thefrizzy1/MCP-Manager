import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'

const listeners = new Set<() => void>()

export function getTheme(): Theme {
  return (document.documentElement.getAttribute('data-theme') as Theme) || 'dark'
}

export function setTheme(t: Theme): void {
  document.documentElement.setAttribute('data-theme', t)
  try {
    localStorage.setItem('plutus_theme', t)
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l())
}

export function toggleTheme(): void {
  setTheme(getTheme() === 'dark' ? 'light' : 'dark')
}

/** Subscribe a component to theme changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    getTheme,
    () => 'dark' as Theme,
  )
}
