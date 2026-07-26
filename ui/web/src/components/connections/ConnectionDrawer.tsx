import { useState } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Drawer } from '@/components/ui/Drawer'
import { Button } from '@/components/ui/Button'
import { Tag } from '@/components/ui/Tag'
import { HealthBadge } from '@/components/ui/StatusDot'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { serviceHealth, type HealthState, type Service } from '@/lib/health'

type Tab = 'overview' | 'tools' | 'test'

export function ConnectionDrawer({
  service,
  onClose,
  onConfigure,
  onChanged,
}: {
  service: Service
  onClose: () => void
  onConfigure: (id: string) => void
  onChanged: () => void
}) {
  const s = service
  const st = serviceHealth(s) as HealthState
  const [tab, setTab] = useState<Tab>('overview')
  const [out, setOut] = useState('Not run yet.')
  const [busy, setBusy] = useState(false)

  async function run(mode: 'probe' | 'tools') {
    setBusy(true)
    setOut('Running…')
    try {
      const d =
        mode === 'tools'
          ? await api.post<{ output?: string; summary?: string }>(`/service/smoke-tools/${s.id}`)
          : await api.get<{ output?: string; detail?: string; summary?: string }>(`/service/test/${s.id}`)
      setOut(d.output || d.summary || (d as { detail?: string }).detail || '(no output)')
      onChanged()
    } catch (e) {
      setOut(String(e))
    } finally {
      setBusy(false)
    }
  }

  const tabs: Tab[] = ['overview', 'tools', 'test']

  return (
    <Drawer
      open
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          <ServiceLogo id={s.id} label={s.label} domain={s.logo_domain} size={22} />
          {s.label}
        </span>
      }
    >
      <div className="mb-3 flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-2.5 py-1.5 text-[12.5px] font-medium capitalize',
              tab === t ? 'border-b-2 border-accent text-accent' : 'text-ink-3 hover:text-ink',
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="space-y-4">
          <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className="text-ink-3">ID</dt>
            <dd className="font-mono text-ink">{s.id}</dd>
            <dt className="text-ink-3">Category</dt>
            <dd>
              <Tag>{s.tag || s.section || '—'}</Tag>
            </dd>
            {s.url && (
              <>
                <dt className="text-ink-3">Address</dt>
                <dd className="min-w-0">
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="break-all font-mono text-accent">
                    {s.url}
                  </a>
                </dd>
              </>
            )}
            <dt className="text-ink-3">Status</dt>
            <dd>
              <HealthBadge state={st} />
            </dd>
            <dt className="text-ink-3">Configured</dt>
            <dd className="text-ink">{s.configured ? 'Yes' : 'No'}</dd>
            <dt className="text-ink-3">Tools</dt>
            <dd className="text-ink">{s.tool_count ?? 0}</dd>
          </dl>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" onClick={() => onConfigure(s.id)}>
              Configure
            </Button>
            <Button variant="default" size="sm" disabled={busy} onClick={() => run('probe')}>
              Test
            </Button>
            <Button variant="default" size="sm" disabled={busy} onClick={() => run('tools')}>
              Try tools
            </Button>
            <Button
              variant={s.ignored ? 'default' : 'danger'}
              size="sm"
              onClick={async () => {
                await api.post(`/api/v1/service/${s.id}/ignore`, { ignored: !s.ignored })
                onChanged()
                onClose()
              }}
            >
              {s.ignored ? 'Restore' : 'Ignore'}
            </Button>
          </div>
        </div>
      )}

      {tab === 'tools' && (
        <div className="flex flex-wrap gap-1.5">
          {(s.tool_names ?? []).length ? (
            (s.tool_names ?? []).map((t) => (
              <span key={t} className="rounded-[var(--radius-sm)] border border-border bg-surface-2 px-2 py-1 font-mono text-[11.5px] text-ink-2">
                {t}
              </span>
            ))
          ) : (
            <p className="text-[13px] text-ink-3">No tools listed for this connection.</p>
          )}
        </div>
      )}

      {tab === 'test' && (
        <div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" disabled={busy} onClick={() => run('probe')}>
              HTTP probe
            </Button>
            <Button variant="default" size="sm" disabled={busy} onClick={() => run('tools')}>
              Try each tool
            </Button>
          </div>
          <pre className="mt-3 max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2.5 font-mono text-[11.5px] text-ink-2">
            {out}
          </pre>
        </div>
      )}
    </Drawer>
  )
}
