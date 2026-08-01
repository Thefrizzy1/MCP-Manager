import type { ReactNode } from 'react'

export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-6">
      <div className="min-w-0">
        <h1 className="text-[21px] font-bold leading-tight tracking-[-0.02em] text-ink">{title}</h1>
        {subtitle && <p className="text-[12.5px] text-ink-3">{subtitle}</p>}
      </div>
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** Scrollable content region below the PageHead. */
export function PageBody({ children }: { children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="page-enter mx-auto max-w-[1200px] px-6 py-5">{children}</div>
    </div>
  )
}
