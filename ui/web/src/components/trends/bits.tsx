import { cn } from '@/lib/cn'
import { STAGES, type Backtest } from './types'

const ACTION_TONE: Record<string, string> = {
  'PREP NOW': 'border-accent/30 bg-accent-weak text-accent',
  'SHOOT NOW': 'border-ok/30 bg-ok-weak text-ok',
  'SHOOT: 4GB ANGLE': 'border-ok/30 bg-ok-weak text-ok',
  LATE: 'border-warn/30 bg-warn-weak text-warn',
  DONE: 'border-border bg-surface-2 text-ink-3',
  WATCH: 'border-border bg-surface-2 text-ink-2',
}

export function ActionTag({ action }: { action: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold tracking-wide',
        ACTION_TONE[action] ?? ACTION_TONE.WATCH,
      )}
    >
      {action}
    </span>
  )
}

export function HotBar({ hot, threshold }: { hot: number; threshold?: number }) {
  const tone = hot >= (threshold ?? 70) ? 'bg-danger' : hot >= 50 ? 'bg-warn' : hot >= 30 ? 'bg-accent' : 'bg-ink-3'
  return (
    <div className="flex items-center gap-2">
      <span className="w-7 text-right text-[13px] font-semibold tabular-nums text-ink">{Math.round(hot)}</span>
      <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-surface-hover">
        <div className={cn('absolute inset-y-0 left-0 rounded-full', tone)} style={{ width: `${Math.min(hot, 100)}%` }} />
        {threshold !== undefined && (
          <div className="absolute inset-y-0 w-px bg-ink-2/60" style={{ left: `${threshold}%` }} />
        )}
      </div>
    </div>
  )
}

/** rumor → gguf as six dots, filled up to the current stage. */
export function StageTrack({ stage }: { stage: string }) {
  const idx = STAGES.indexOf(stage as (typeof STAGES)[number])
  return (
    <div className="flex items-center gap-1.5" title={`Stage: ${stage}`}>
      <div className="flex gap-[3px]">
        {STAGES.map((s, i) => (
          <span
            key={s}
            className={cn(
              'h-2 w-2 rounded-full',
              i <= idx ? (i >= 4 ? 'bg-ok' : i === 3 ? 'bg-accent' : 'bg-ink-2') : 'bg-surface-hover',
            )}
          />
        ))}
      </div>
      <span className="text-[11.5px] text-ink-2">{stage}</span>
    </div>
  )
}

/** One 0..1 component as a labelled meter. */
export function Meter({ label, value, hint, negative }: { label: string; value?: number; hint?: string; negative?: boolean }) {
  const v = Math.max(0, Math.min(1, value ?? 0))
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11.5px]">
        <span className="text-ink-2">{label}</span>
        <span className="tabular-nums text-ink">{Math.round(v * 100)}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-hover">
        <div className={cn('h-full rounded-full', negative ? 'bg-warn' : 'bg-accent')} style={{ width: `${v * 100}%` }} />
      </div>
      {hint && <div className="mt-0.5 text-[10.5px] text-ink-3">{hint}</div>}
    </div>
  )
}

/** The replay as a line, with the moments that matter marked on it. */
export function BacktestChart({ bt }: { bt: Backtest }) {
  const s = bt.series ?? []
  if (s.length < 2) return <div className="text-[12px] text-ink-3">Not enough history to replay yet.</div>
  const W = 460
  const H = 120
  const pad = 6
  const t0 = s[0].t
  const t1 = s[s.length - 1].t
  const x = (t: number) => pad + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * pad)
  const y = (h: number) => H - pad - (h / 100) * (H - 2 * pad)
  const path = s.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.hot).toFixed(1)}`).join(' ')
  const marks: { t: number | null; label: string; color: string }[] = [
    { t: bt.alert, label: 'alert', color: 'var(--danger)' },
    { t: bt.release, label: 'weights', color: 'var(--ok)' },
    { t: bt.first_competitor_video, label: '1st video', color: 'var(--warn)' },
    { t: bt.own_upload, label: 'you', color: 'var(--accent)' },
  ]
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Hot score replay">
        <line x1={pad} x2={W - pad} y1={y(bt.threshold)} y2={y(bt.threshold)} stroke="var(--border-strong)" strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="var(--ink-2)" strokeWidth={1.5} />
        {marks.map((m) =>
          m.t && m.t >= t0 && m.t <= t1 ? (
            <g key={m.label}>
              <line x1={x(m.t)} x2={x(m.t)} y1={pad} y2={H - pad} stroke={m.color} strokeWidth={1.5} />
            </g>
          ) : null,
        )}
      </svg>
      <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-ink-3">
        {marks.map((m) => (
          <span key={m.label} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: m.color }} />
            {m.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-px w-3 border-t border-dashed border-ink-3" /> threshold {bt.threshold}
        </span>
      </div>
    </div>
  )
}

export function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="mb-5 mt-4 first:mt-0">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ink-3">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}
