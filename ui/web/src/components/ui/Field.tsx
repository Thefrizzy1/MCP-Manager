import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label?: ReactNode
  hint?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <label className={cn('block', className)}>
      {label && <div className="mb-1 text-[12px] font-medium text-ink-2">{label}</div>}
      {children}
      {hint && <div className="mt-1 text-[11.5px] text-ink-3">{hint}</div>}
    </label>
  )
}

/** A horizontal label + control row, for settings-style forms. */
export function Row({
  label,
  hint,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-2">
      <div className="w-40 shrink-0">
        <div className="text-[13px] text-ink">{label}</div>
        {hint && <div className="text-[11.5px] text-ink-3">{hint}</div>}
      </div>
      <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
    </div>
  )
}
