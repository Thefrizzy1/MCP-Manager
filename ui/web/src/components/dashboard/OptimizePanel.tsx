import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

interface CatInfo {
  tools: number
  tokens: number
  enabled: boolean
}
interface ExposureReport {
  categories: Record<string, CatInfo>
  disabled_categories: string[]
  total_tools: number
  exposed_tools: number
  total_tokens_est: number
  exposed_tokens_est: number
  tokens_saved_est: number
  percent_saved: number
  restart_note: string
}

export function OptimizePanel() {
  const { data, refetch } = useQuery({
    queryKey: ['tool-exposure'],
    queryFn: () => api.get<ExposureReport>('/api/v1/tools/exposure'),
  })
  const [disabled, setDisabled] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (data) setDisabled(new Set(data.disabled_categories ?? []))
  }, [data])

  // Every read of the report is guarded. The error boundary above will catch a
  // throw, but a card that blanks its own page because one field was missing is
  // still the wrong behaviour — the numbers it cannot compute should read zero
  // and the rest of the dashboard should carry on.
  const cats = useMemo(
    () => Object.entries(data?.categories ?? {}).sort((a, b) => b[1].tokens - a[1].tokens),
    [data],
  )
  const dirty = useMemo(() => {
    if (!data) return false
    const a = [...disabled].sort().join(',')
    const b = [...(data.disabled_categories ?? [])].sort().join(',')
    return a !== b
  }, [disabled, data])

  // Directional live preview (approx; exact figure lands after Save recomputes overlap).
  const livePreview = useMemo(() => {
    if (!data) return null
    let savedTokens = 0
    let hiddenTools = 0
    for (const [name, info] of Object.entries(data.categories ?? {})) {
      if (disabled.has(name)) {
        savedTokens += info.tokens
        hiddenTools += info.tools
      }
    }
    const pct = data.total_tokens_est ? Math.round((savedTokens / data.total_tokens_est) * 100) : 0
    return { savedTokens, hiddenTools, pct }
  }, [disabled, data])

  async function save() {
    setSaving(true)
    setMsg('')
    try {
      await api.post('/api/v1/tools/exposure', { disabled_categories: [...disabled] })
      await refetch()
      setMsg('Saved — restart the MCP server to apply.')
    } catch (e) {
      setMsg(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (!data) return <Card className="p-4 text-[13px] text-ink-3">Loading optimization…</Card>

  const total = data.total_tools ?? 0
  const saved = (dirty ? livePreview!.savedTokens : data.tokens_saved_est) ?? 0
  const pct = (dirty ? livePreview!.pct : data.percent_saved) ?? 0
  const exposed = dirty ? total - livePreview!.hiddenTools : (data.exposed_tools ?? 0)

  return (
    <Card>
      <CardHeader
        title="Tool optimization"
        subtitle="Expose only the tool groups your agents need — smaller manifest, fewer prompt tokens"
        action={
          <div className="text-right">
            <div className="text-[18px] font-semibold tabular-nums text-ink">
              ~{saved.toLocaleString()} <span className="text-[12px] font-normal text-ink-3">tokens saved</span>
            </div>
            <div className="text-[11.5px] text-ink-3">
              {exposed}/{total} tools · {pct}% smaller
            </div>
          </div>
        }
      />
      <div className="px-4 pb-4">
        <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${100 - pct}%` }} />
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
          {cats.map(([name, info]) => {
            const on = !disabled.has(name)
            return (
              <button
                key={name}
                onClick={() =>
                  setDisabled((s) => {
                    const n = new Set(s)
                    n.has(name) ? n.delete(name) : n.add(name)
                    return n
                  })
                }
                className={cn(
                  'flex items-center gap-2 rounded-[var(--radius-sm)] border px-2.5 py-1.5 text-left transition-colors',
                  on ? 'border-border bg-surface-2' : 'border-transparent bg-transparent opacity-55',
                )}
              >
                <span
                  className={cn(
                    'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border',
                    on ? 'border-accent bg-accent text-accent-fg' : 'border-border-strong',
                  )}
                >
                  {on && <Check size={11} strokeWidth={3} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium text-ink">{name}</span>
                  <span className="block text-[10.5px] text-ink-3">
                    {info.tools} tools · ~{info.tokens.toLocaleString()}t
                  </span>
                </span>
              </button>
            )
          })}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <Button variant="primary" size="sm" disabled={!dirty || saving} onClick={save}>
            Save exposure
          </Button>
          {msg ? (
            <span className="text-[12px] text-ink-3">{msg}</span>
          ) : (
            <span className="text-[12px] text-ink-3">Behaviour never changes — only which tools the manifest lists.</span>
          )}
        </div>
      </div>
    </Card>
  )
}
