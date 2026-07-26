import { cn } from '@/lib/cn'
import { HEALTH_META, type HealthState, type Tone } from '@/lib/health'

const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-ink-3',
}
const TONE_TEXT: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
  muted: 'text-ink-3',
}

export function StatusDot({ state, className }: { state: HealthState; className?: string }) {
  const tone = HEALTH_META[state].tone
  return <span className={cn('inline-block h-2 w-2 shrink-0 rounded-full', TONE_DOT[tone], className)} />
}

export function HealthBadge({ state }: { state: HealthState }) {
  const meta = HEALTH_META[state]
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-[12.5px] font-medium', TONE_TEXT[meta.tone])}>
      <span className={cn('inline-block h-2 w-2 rounded-full', TONE_DOT[meta.tone])} />
      {meta.label}
    </span>
  )
}
