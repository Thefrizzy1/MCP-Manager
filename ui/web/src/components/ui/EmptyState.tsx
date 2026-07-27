import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
}: {
  icon: LucideIcon
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius)] bg-surface-2 text-ink-3">
        <Icon size={18} strokeWidth={1.5} />
      </div>
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {hint && <div className="max-w-xs text-[12px] text-ink-3">{hint}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}
