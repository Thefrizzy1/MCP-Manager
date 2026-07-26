import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import type { Tone } from '@/lib/health'

const TONE: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
  muted: 'text-ink',
}

export function Stat({
  label,
  value,
  hint,
  tone = 'muted',
  onClick,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  onClick?: () => void
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      className={cn(
        'rounded-[var(--radius)] border border-border bg-surface px-4 py-3.5 text-left',
        onClick && 'transition-colors hover:border-border-strong hover:bg-surface-hover',
      )}
    >
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-3">{label}</div>
      <div className={cn('mt-1 text-[26px] font-semibold leading-none tabular-nums', TONE[tone])}>{value}</div>
      {hint && <div className="mt-1.5 text-[12px] text-ink-3">{hint}</div>}
    </Tag>
  )
}
