import { NAV_GROUPS, SETTINGS_ITEM, type NavItem } from '@/lib/nav'
import { navigate } from '@/lib/router'
import { cn } from '@/lib/cn'
import { ThemeToggle } from './ThemeToggle'

function NavRow({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate?: () => void }) {
  const Icon = item.icon
  return (
    <button
      onClick={() => {
        navigate(item.id)
        onNavigate?.()
      }}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-2 py-1.5 text-[13px] transition-colors',
        active
          ? 'bg-accent-weak font-medium text-accent'
          : 'text-ink-2 hover:bg-surface-hover hover:text-ink',
      )}
    >
      <Icon
        size={16}
        strokeWidth={active ? 2.2 : 1.75}
        className={active ? 'text-accent' : 'text-ink-3 group-hover:text-ink-2'}
      />
      <span>{item.label}</span>
    </button>
  )
}

export function Sidebar({ route, onNavigate }: { route: string; onNavigate?: () => void }) {
  return (
    <aside className="flex h-full w-[224px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-[var(--radius)] bg-accent text-[13px] font-bold text-accent-fg">
          P
        </div>
        <div className="leading-tight">
          <div className="text-[13px] font-semibold text-ink">Plutus</div>
          <div className="text-[11px] text-ink-3">MCP Manager</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2.5 py-3">
        {NAV_GROUPS.map((g) => (
          <div key={g.label} className="mb-4">
            <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
              {g.label}
            </div>
            <div className="space-y-0.5">
              {g.items.map((it) => (
                <NavRow key={it.id} item={it} active={route === it.id} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="space-y-0.5 border-t border-border p-2.5">
        <NavRow item={SETTINGS_ITEM} active={route === 'settings'} onNavigate={onNavigate} />
        <div className="flex items-center justify-between px-2 pt-1">
          <span className="text-[11px] text-ink-3">Theme</span>
          <ThemeToggle />
        </div>
      </div>
    </aside>
  )
}
