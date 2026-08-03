import { useState, type ReactNode } from 'react'
import { useIsFetching } from '@tanstack/react-query'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { DefaultPasswordBanner } from './DefaultPasswordBanner'

export function Shell({ route, children }: { route: string; children: ReactNode }) {
  const [mobileNav, setMobileNav] = useState(false)
  const fetching = useIsFetching()
  return (
    <div className="flex h-full">
      {/* Desktop sidebar */}
      <div className="hidden md:block">
        <Sidebar route={route} />
      </div>

      {/* Mobile drawer */}
      {mobileNav && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMobileNav(false)}>
          <div className="absolute inset-0 bg-black/45" />
          <div className="absolute left-0 top-0 h-full" onClick={(e) => e.stopPropagation()}>
            <Sidebar route={route} onNavigate={() => setMobileNav(false)} />
          </div>
        </div>
      )}

      <main className="relative flex min-w-0 flex-1 flex-col">
        {/* Subtle liveness: a thin bar rides the top while any query is in flight. */}
        {fetching > 0 && <span className="plutus-activity-bar" aria-hidden />}
        {/* Mobile top bar */}
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3 md:hidden">
          <button
            onClick={() => setMobileNav(true)}
            aria-label="Open menu"
            className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] text-ink-2 hover:bg-surface-hover"
          >
            <Menu size={18} />
          </button>
          <span className="text-[14px] font-semibold text-ink">Plutus</span>
        </div>
        <DefaultPasswordBanner />
        {children}
      </main>
    </div>
  )
}
