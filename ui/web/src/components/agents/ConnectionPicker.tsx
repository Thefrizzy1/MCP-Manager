import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import type { Service } from '@/lib/health'
import { cn } from '@/lib/cn'
import { ServiceLogo } from '@/components/ui/ServiceLogo'

/** Scrollable multi-select for the MCP connections an agent may use.
 *  Trigger shows the count; the panel has Select all / none, a filter,
 *  and a scrolling checkbox list — works for 4 connections or 40. */
export function ConnectionPicker({
  connections,
  selected,
  onChange,
}: {
  connections: Service[]
  selected: Record<string, boolean>
  onChange: (next: Record<string, boolean>) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const total = connections.length
  const count = connections.filter((c) => selected[c.id]).length
  const needle = q.trim().toLowerCase()
  const filtered = needle ? connections.filter((c) => c.label.toLowerCase().includes(needle)) : connections

  const setAll = (val: boolean) => onChange(Object.fromEntries(connections.map((c) => [c.id, val])))
  const toggle = (id: string) => onChange({ ...selected, [id]: !selected[id] })

  const label =
    count === 0 ? 'None selected' : count === total ? `All ${total} selected` : `${count} of ${total} selected`

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-full items-center justify-between rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2.5 text-[13px] text-ink transition-colors hover:border-accent/60 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
      >
        <span className={cn(count === 0 && 'text-ink-3')}>{label}</span>
        <ChevronDown size={14} className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-full overflow-hidden rounded-[var(--radius-md)] border border-border-strong bg-surface shadow-lg">
          <div className="flex items-center gap-1.5 border-b border-border px-2 py-1.5">
            <button
              type="button"
              onClick={() => setAll(true)}
              className="rounded px-1.5 py-0.5 text-[12px] font-medium text-accent hover:bg-surface-hover"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => setAll(false)}
              className="rounded px-1.5 py-0.5 text-[12px] text-ink-2 hover:bg-surface-hover"
            >
              Select none
            </button>
            <span className="ml-auto text-[11.5px] text-ink-3">
              {count}/{total}
            </span>
          </div>

          {total > 6 && (
            <div className="border-b border-border px-2 py-1.5">
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Filter connections…"
                className="h-7 w-full rounded-[var(--radius-sm)] border border-border bg-surface-2 px-2 text-[12px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
              />
            </div>
          )}

          <div className="max-h-64 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="px-3 py-2 text-[12px] text-ink-3">No matches.</p>
            ) : (
              filtered.map((c) => {
                const on = !!selected[c.id]
                return (
                  <button
                    type="button"
                    key={c.id}
                    onClick={() => toggle(c.id)}
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-hover"
                  >
                    <span
                      className={cn(
                        'flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors',
                        on ? 'border-accent bg-accent text-white' : 'border-border-strong',
                      )}
                    >
                      {on && <Check size={11} strokeWidth={3} />}
                    </span>
                    <ServiceLogo id={c.id} label={c.label} size={20} domain={c.logo_domain} />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink-2">{c.label}</span>
                  </button>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
