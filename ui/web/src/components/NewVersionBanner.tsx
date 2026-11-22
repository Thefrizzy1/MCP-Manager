import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

/** The script this tab is running, e.g. "index-Bd-JCNuO.js". */
function runningBuild(): string {
  const el = document.querySelector<HTMLScriptElement>('script[src*="/assets/index-"]')
  return el ? (el.src.split('/').pop() ?? '') : ''
}

/** After an update the server holds a newer build than an open tab is running.
 *  Nothing reloads by itself (that would throw away what is on screen); this
 *  says so and offers the reload. */
export function NewVersionBanner() {
  const [stale, setStale] = useState(false)
  useEffect(() => {
    const mine = runningBuild()
    if (!mine) return
    const check = async () => {
      try {
        const r = await fetch('/server/health', { cache: 'no-store' })
        const d = (await r.json()) as { build?: string }
        if (d.build && d.build !== mine) setStale(true)
      } catch {
        /* server restarting: try again next time */
      }
    }
    check()
    const id = window.setInterval(check, 60_000)
    return () => window.clearInterval(id)
  }, [])
  if (!stale) return null
  return (
    <div className="flex items-center gap-2.5 border-b border-accent/40 bg-accent-weak px-4 py-2 text-[12.5px] text-ink">
      <RefreshCw size={14} className="shrink-0 text-accent" />
      <span>Plutus was updated. This tab is still showing the old version.</span>
      <button
        onClick={() => window.location.reload()}
        className="ml-auto shrink-0 rounded-[var(--radius-sm)] bg-accent px-2.5 py-1 text-[12px] font-medium text-accent-fg hover:bg-accent-hover"
      >
        Reload
      </button>
    </div>
  )
}
