import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'

interface Tool {
  name: string
  label: string
  enabled: boolean
}
interface Group {
  id: string
  label: string
  desc: string
  tools: Tool[]
}
interface Resp {
  groups: Group[]
  total: number
  disabled: string[]
  restart_note: string
}

/** Per-API switches for the single "Public APIs" card.
 *  A disabled API is not registered on the served /mcp instance at all, so this
 *  shrinks the tool manifest (and its per-request token cost), not just the UI. */
export function PublicApisManager() {
  const toast = useToast()
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['public-apis'],
    queryFn: () => api.get<Resp>('/api/v1/public-apis'),
  })
  const [off, setOff] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (data) {
      setOff(new Set(data.disabled))
      setDirty(false)
    }
  }, [data])

  const toggle = (name: string) => {
    setOff((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
    setDirty(true)
  }

  function setGroup(g: Group, enabled: boolean) {
    setOff((prev) => {
      const next = new Set(prev)
      for (const t of g.tools) {
        if (enabled) next.delete(t.name)
        else next.add(t.name)
      }
      return next
    })
    setDirty(true)
  }

  async function save() {
    setSaving(true)
    try {
      await api.post('/api/v1/public-apis', { disabled_tools: [...off] })
      toast.success(`Saved — ${data ? data.total - off.size : 0} APIs exposed. Restart to apply.`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (isLoading) return <p className="text-[13px] text-ink-3">Loading…</p>

  const groups = data?.groups ?? []
  const total = data?.total ?? 0
  const on = total - off.size

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12.5px] text-ink-2">
          {on} of {total} APIs exposed
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button variant="ghost" size="sm" onClick={() => { setOff(new Set()); setDirty(true) }}>
            Enable all
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setOff(new Set(groups.flatMap((g) => g.tools.map((t) => t.name))))
              setDirty(true)
            }}
          >
            Disable all
          </Button>
        </div>
      </div>

      <p className="text-[11.5px] text-ink-3">
        Disabled APIs are dropped from the MCP tool manifest entirely — that lowers the tool count and the
        tokens every request carries. {data?.restart_note}
      </p>

      <div className="max-h-[46vh] space-y-3 overflow-y-auto pr-1">
        {groups.map((g) => {
          const groupOn = g.tools.filter((t) => !off.has(t.name)).length
          return (
            <div key={g.id} className="rounded-[var(--radius-sm)] border border-border p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <strong className="text-[12.5px] text-ink">{g.label}</strong>
                <span className="text-[11px] text-ink-3">
                  {groupOn}/{g.tools.length}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <button className="text-[11px] text-accent hover:underline" onClick={() => setGroup(g, true)}>
                    all
                  </button>
                  <span className="text-[11px] text-ink-3">·</span>
                  <button className="text-[11px] text-ink-2 hover:underline" onClick={() => setGroup(g, false)}>
                    none
                  </button>
                </div>
              </div>
              {g.desc && <p className="mt-0.5 text-[11px] text-ink-3">{g.desc}</p>}
              <div className="mt-1.5 grid gap-1 sm:grid-cols-2">
                {g.tools.map((t) => (
                  <label key={t.name} className="flex items-center gap-1.5 text-[12px] text-ink-2">
                    <input type="checkbox" checked={!off.has(t.name)} onChange={() => toggle(t.name)} />
                    <span className="truncate" title={t.name}>
                      {t.label}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      <Button variant="primary" size="sm" disabled={saving || !dirty} onClick={save}>
        {saving ? 'Saving…' : 'Save selection'}
      </Button>
    </div>
  )
}
