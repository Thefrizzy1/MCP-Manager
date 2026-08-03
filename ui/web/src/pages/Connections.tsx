import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, ChevronsUpDown, ExternalLink, Grid3x3, Plus, RefreshCw, RotateCcw, Search, Settings2 } from 'lucide-react'
import { api } from '@/lib/api'
import { serviceHealth, type HealthState, type Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { HealthBadge } from '@/components/ui/StatusDot'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { ConfigureModal } from '@/components/connections/ConfigureModal'
import { AddConnectionModal } from '@/components/connections/AddConnectionModal'
import { CatalogModal } from '@/components/connections/CatalogModal'
import { ConnectionDrawer } from '@/components/connections/ConnectionDrawer'
import { useToast } from '@/components/ui/Toast'

const STATUS_FILTERS = [
  { value: '', label: 'All statuses' },
  { value: 'online', label: 'Online' },
  { value: 'offline', label: 'Offline' },
  { value: 'unconfigured', label: 'Not configured' },
  { value: 'disabled', label: 'Disabled' },
]
const STATUS_RANK: Record<string, number> = {
  online: 0, unknown: 1, api_error: 2, auth_error: 2, rate_limited: 2, offline: 3, unconfigured: 4, disabled: 5,
}
const COLS: { key: string; label: string; num?: boolean }[] = [
  { key: 'name', label: 'Service' },
  { key: 'section', label: 'Category' },
  { key: 'tool_count', label: 'Tools', num: true },
  { key: 'url', label: 'Address' },
  { key: 'status', label: 'Status' },
]

function cmp(a: Service, b: Service, col: string): number {
  if (col === 'tool_count') return (a.tool_count || 0) - (b.tool_count || 0)
  if (col === 'status') return (STATUS_RANK[serviceHealth(a)] ?? 9) - (STATUS_RANK[serviceHealth(b)] ?? 9)
  if (col === 'section') return (a.tag || a.section || '').localeCompare(b.tag || b.section || '')
  if (col === 'url') return (a.url || '').localeCompare(b.url || '')
  return (a.label || '').localeCompare(b.label || '')
}

export function Connections() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['services'],
    queryFn: () => api.get<{ services?: Service[] }>('/api/v1/dashboard?sections=services'),
  })
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [hideUnconfigured, setHideUnconfigured] = useState(false)
  const [showIgnored, setShowIgnored] = useState(false)
  const [busy, setBusy] = useState<Record<string, string>>({})
  const [configureId, setConfigureId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [addInitialLabel, setAddInitialLabel] = useState('')
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [drawer, setDrawer] = useState<Service | null>(null)
  const [report, setReport] = useState<string | null>(null)
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 }>({ col: 'status', dir: 1 })

  const services = data?.services ?? []
  const refresh = () => qc.invalidateQueries({ queryKey: ['services'] })

  const rows = useMemo(() => {
    const ql = q.toLowerCase()
    const filtered = services.filter((s) => {
      if (s.ignored && !showIgnored) return false
      if (hideUnconfigured && !s.configured) return false
      if (status && serviceHealth(s) !== status) return false
      if (ql && !`${s.label} ${s.id} ${s.section} ${s.tag} ${s.url}`.toLowerCase().includes(ql)) return false
      return true
    })
    return filtered.sort((a, b) => sort.dir * cmp(a, b, sort.col))
  }, [services, q, status, hideUnconfigured, showIgnored, sort])

  const active = services.filter((s) => !s.ignored)

  function toggleSort(col: string) {
    setSort((s) => (s.col === col ? { col, dir: (s.dir === 1 ? -1 : 1) as 1 | -1 } : { col, dir: 1 }))
  }

  async function act(id: string, kind: string, fn: () => Promise<unknown>) {
    setBusy((b) => ({ ...b, [id]: kind }))
    try {
      await fn()
      refresh()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy((b) => {
        const n = { ...b }
        delete n[id]
        return n
      })
    }
  }

  async function testOne(s: Service) {
    setBusy((b) => ({ ...b, [s.id]: 'test' }))
    try {
      const d = await api.get<{ output?: string; summary?: string; state?: string; status_code?: number }>(
        `/service/test/${s.id}`,
      )
      const st = d.state ? `State: ${d.state}${d.status_code ? ` (HTTP ${d.status_code})` : ''}\n\n` : ''
      setReport(`${s.label} — HTTP reachability probe\n\n${st}${d.output || d.summary || '(no output)'}`)
      refresh()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy((b) => {
        const n = { ...b }
        delete n[s.id]
        return n
      })
    }
  }

  async function tryOne(s: Service) {
    setBusy((b) => ({ ...b, [s.id]: 'try' }))
    try {
      const d = await api.post<{ output?: string }>(`/service/smoke-tools/${s.id}`)
      setReport(`${s.label} — tool smoke test\n\n${d.output || '(no output)'}`)
      refresh()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy((b) => {
        const n = { ...b }
        delete n[s.id]
        return n
      })
    }
  }

  async function testAll(btn: HTMLButtonElement) {
    btn.disabled = true
    try {
      const d = await api.post<{ markdown?: string }>('/health/full-report')
      setReport(d.markdown || '(no report)')
      refresh()
    } catch (e) {
      toast.error(String(e))
    } finally {
      btn.disabled = false
    }
  }

  return (
    <>
      <PageHead
        title="Connections"
        subtitle="Every service this MCP server can reach"
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={() => refresh()}>
              <RefreshCw size={14} /> Refresh
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setCatalogOpen(true)}>
              <Grid3x3 size={14} /> Catalog
            </Button>
            <Button variant="ghost" size="sm" onClick={(e) => testAll(e.currentTarget)}>
              Test all
            </Button>
            <Button variant="primary" size="sm" onClick={() => { setAddInitialLabel(''); setAddOpen(true) }}>
              <Plus size={14} /> Add
            </Button>
          </>
        }
      />
      <PageBody>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="h-8 w-56 pl-8" />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </Select>
          <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={hideUnconfigured} onChange={(e) => setHideUnconfigured(e.target.checked)} /> Hide unconfigured
          </label>
          <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} /> Show ignored
          </label>
          <span className="ml-auto text-[12px] text-ink-3">{rows.length} shown · {active.length} active</span>
        </div>

        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-ink-3">
                  {COLS.map((c) => (
                    <th
                      key={c.key}
                      onClick={() => toggleSort(c.key)}
                      className={`cursor-pointer select-none px-3 py-2 font-semibold hover:text-ink ${c.num ? 'text-right' : ''}`}
                    >
                      <span className="inline-flex items-center gap-1">
                        {c.label}
                        <ChevronsUpDown size={11} className={sort.col === c.key ? 'text-accent' : 'text-ink-3/50'} />
                      </span>
                    </th>
                  ))}
                  <th className="px-3 py-2 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={6} className="px-3 py-6 text-ink-3">Loading…</td></tr>
                ) : isError ? (
                  <tr><td colSpan={6} className="px-3 py-6 text-danger">Couldn’t load connections.</td></tr>
                ) : rows.length === 0 ? (
                  <tr><td colSpan={6} className="px-3 py-6 text-ink-3">No connections match.</td></tr>
                ) : (
                  rows.map((s) => {
                    const st = serviceHealth(s) as HealthState
                    const host = (s.url || '').replace(/^https?:\/\//, '')
                    return (
                      <tr key={s.id} className={`border-b border-border last:border-0 hover:bg-surface-hover ${s.ignored ? 'opacity-45' : ''}`}>
                        <td className="px-3 py-2.5">
                          <button className="flex items-center gap-2.5 text-left" onClick={() => setDrawer(s)}>
                            <ServiceLogo id={s.id} label={s.label} domain={s.logo_domain} emoji={s.icon} />
                            <span className="min-w-0">
                              <span className="block truncate font-medium text-ink">{s.label}</span>
                              <span className="block truncate text-[11.5px] text-ink-3">{s.id}</span>
                            </span>
                          </button>
                        </td>
                        <td className="px-3 py-2.5"><Tag>{s.tag || s.section || '—'}</Tag></td>
                        <td className="px-3 py-2.5 text-right font-mono text-[12px] text-ink-2">{s.tool_count ?? 0}</td>
                        <td className="px-3 py-2.5">
                          {s.url ? (
                            <a href={s.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-mono text-[12px] text-ink-2 hover:text-accent" title={s.url}>
                              {host.length > 30 ? host.slice(0, 29) + '…' : host}
                              <ExternalLink size={11} />
                            </a>
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5"><HealthBadge state={st} /></td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center justify-end gap-1">
                            <Button variant="ghost" size="sm" disabled={!!busy[s.id]} onClick={() => testOne(s)} title="Probe the real HTTP address and show the result">
                              {busy[s.id] === 'test' ? '…' : 'Test'}
                            </Button>
                            <Button variant="ghost" size="sm" disabled={!!busy[s.id]} onClick={() => tryOne(s)} title="Actually call this service's tools and show pass/fail">
                              {busy[s.id] === 'try' ? '…' : 'Try'}
                            </Button>
                            <Button variant="ghost" size="icon-sm" title="Configure" onClick={() => setConfigureId(s.id)}>
                              <Settings2 size={14} />
                            </Button>
                            <Button variant="ghost" size="icon-sm" title={s.ignored ? 'Restore' : 'Ignore'} onClick={() => act(s.id, 'ignore', () => api.post(`/api/v1/service/${s.id}/ignore`, { ignored: !s.ignored }))}>
                              {s.ignored ? <RotateCcw size={14} /> : <Ban size={14} />}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </PageBody>

      {configureId && (
        <ConfigureModal id={configureId} onClose={() => setConfigureId(null)} onSaved={() => { setConfigureId(null); refresh() }} />
      )}
      {addOpen && (
        <AddConnectionModal initialLabel={addInitialLabel} onClose={() => setAddOpen(false)} onAdded={() => { setAddOpen(false); refresh() }} />
      )}
      {catalogOpen && (
        <CatalogModal onClose={() => setCatalogOpen(false)} onPick={(name) => { setCatalogOpen(false); setAddInitialLabel(name); setAddOpen(true) }} />
      )}
      {drawer && (
        <ConnectionDrawer service={drawer} onClose={() => setDrawer(null)} onConfigure={(id) => { setDrawer(null); setConfigureId(id) }} onChanged={refresh} />
      )}
      {report !== null && <ReportModal report={report} onClose={() => setReport(null)} />}
    </>
  )
}

function ReportModal({ report, onClose }: { report: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/45 p-4 pt-[8vh]" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-[720px] rounded-[var(--radius-lg)] border border-border bg-surface shadow-[var(--shadow-pop)]">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-[15px] font-semibold text-ink">Health report</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
        </div>
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap px-4 py-4 font-mono text-[12px] text-ink-2">{report}</pre>
      </div>
    </div>
  )
}
