import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-ink-2',
        className,
      )}
    >
      {children}
    </span>
  )
}
