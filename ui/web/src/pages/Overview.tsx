import { useQuery } from '@tanstack/react-query'
import { CalendarDays, Copy, Cpu, HardDrive, MemoryStick, Monitor } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'

interface Machine {
  id: string
  name: string
  role?: string
  can?: string[]
  online: boolean
  here?: boolean
  seen: number
  doing?: string
  next?: string
  specs?: {
    cores?: number
    ram_gb?: number
    gpu?: string
    vram_gb?: number
    vram_used_gb?: number
    gpu_util?: number
    disk_free_gb?: number
    disk_total_gb?: number
    load?: number | null
  }
}
interface Worker {
  id: string
  label: string
  about: string
  abilities: { text: string; state: 'running' | 'ready' | 'planned' }[]
  active: boolean
  busy: boolean
  text: string
  ts: number
  next: string
}
interface Event {
  id: string
  date: string
  title: string
  kind: string
  link?: string
}
interface Data {
  machines: Machine[]
  workers: Worker[]
  calendar: { path: string; events: number; upcoming: Event[] }
}

const WORKER_COLOR: Record<string, string> = {
  researcher: 'var(--room-indigo)',
  workflow_maker: 'var(--room-violet)',
  editor: 'var(--room-rose)',
  manager: 'var(--room-amber)',
  publisher: 'var(--room-lime)',
}
const ABILITY: Record<string, { dot: string; label: string }> = {
  running: { dot: 'var(--ok)', label: 'runs by itself' },
  ready: { dot: 'var(--accent)', label: 'on request' },
  planned: { dot: 'var(--border-strong)', label: 'not built yet' },
}

function ago(ts?: number) {
  if (!ts) return 'never'
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`
}

function Chip({ icon: Icon, children }: { icon: typeof Cpu; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[11px] text-ink-2">
      <Icon size={11} /> {children}
    </span>
  )
}

function MachineCard({ m }: { m: Machine }) {
  const s = m.specs ?? {}
  const working = m.online && !!m.doing
  return (
    <Card className={cn('px-4 py-3.5', !m.online && 'opacity-55')}>
      <div className="flex items-center gap-2">
        <span
          className={cn('h-2.5 w-2.5 rounded-full', working && 'stage-live')}
          style={{ background: working ? 'var(--room-violet)' : m.online ? 'var(--ok)' : 'var(--border-strong)' }}
        />
        <span className="text-[14px] font-semibold text-ink">{m.name}</span>
        <span className="ml-auto text-[11px] text-ink-3">
          {m.here ? 'this server' : m.online ? 'online' : m.seen ? `last seen ${ago(m.seen)}` : 'not reporting yet'}
        </span>
      </div>
      {m.role && <p className="mt-1 text-[12px] text-ink-3">{m.role}</p>}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {s.cores ? <Chip icon={Cpu}>{s.cores} cores</Chip> : null}
        {s.ram_gb ? <Chip icon={MemoryStick}>{s.ram_gb} GB RAM</Chip> : null}
        {s.gpu ? (
          <Chip icon={Monitor}>
            {s.gpu}
            {s.vram_gb ? ` · ${s.vram_used_gb ?? 0}/${s.vram_gb} GB` : ''}
            {s.gpu_util != null ? ` · ${s.gpu_util}%` : ''}
          </Chip>
        ) : null}
        {s.disk_total_gb ? (
          <Chip icon={HardDrive}>
            {s.disk_free_gb} GB free of {s.disk_total_gb}
          </Chip>
        ) : null}
      </div>
      {m.can && m.can.length > 0 && (
        <ul className="mt-2.5 space-y-0.5 text-[12px] text-ink-2">
          {m.can.map((c) => (
            <li key={c}>· {c}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 border-t border-border pt-2.5 text-[12.5px]">
        <div className={working ? 'text-ink' : 'text-ink-3'}>
          <span className="text-ink-3">Now:</span> {m.online ? m.doing || 'idle' : 'offline'}
        </div>
        {m.next && (
          <div className="text-ink-2">
            <span className="text-ink-3">Next:</span> {m.next}
          </div>
        )}
      </div>
    </Card>
  )
}

function WorkerCard({ w }: { w: Worker }) {
  const color = WORKER_COLOR[w.id] ?? 'var(--accent)'
  return (
    <Card className={cn('px-4 py-3.5', !w.active && 'opacity-50 grayscale')}>
      <div className="flex items-center gap-2.5">
        <span
          className={cn('flex h-8 w-8 items-center justify-center rounded-full text-[13px] font-bold text-white', w.busy && 'stage-live')}
          style={{ background: w.active ? color : 'var(--border-strong)' }}
        >
          {w.label[0]}
        </span>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-ink">{w.label}</div>
          <div className="truncate text-[11.5px] text-ink-3">
            {!w.active ? 'not set up yet' : w.busy ? 'working now' : w.ts ? `last worked ${ago(w.ts)}` : 'idle'}
          </div>
        </div>
      </div>
      <p className="mt-2 text-[12px] text-ink-3">{w.about}</p>
      {w.text && (
        <div className="mt-2 rounded-[var(--radius-sm)] bg-surface-2 px-2.5 py-1.5 text-[12.5px] text-ink">
          {w.text}
          {w.next && <div className="text-[11.5px] text-ink-3">Next: {w.next}</div>}
        </div>
      )}
      <ul className="mt-2.5 space-y-1">
        {w.abilities.map((a) => (
          <li key={a.text} className="flex items-start gap-2 text-[12px] text-ink-2" title={ABILITY[a.state].label}>
            <span className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: ABILITY[a.state].dot }} />
            <span className={a.state === 'planned' ? 'text-ink-3' : ''}>{a.text}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function Overview() {
  const toast = useToast()
  const { data } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Data>('/api/v1/overview'),
    refetchInterval: 20_000,
  })
  const feed = data ? `${window.location.origin}${data.calendar.path}` : ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(feed)
      toast.success('Calendar link copied.')
    } catch {
      toast.error('Could not copy. Select the link and copy it by hand.')
    }
  }

  return (
    <>
      <PageHead title="Overview" />
      <PageBody>
        {!data ? (
          <p className="text-[13px] text-ink-3">Loading…</p>
        ) : (
          <div className="space-y-6">
            <section>
              <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Workers</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.workers.map((w) => (
                  <WorkerCard key={w.id} w={w} />
                ))}
              </div>
              <div className="mt-2 flex gap-4 text-[11px] text-ink-3">
                {Object.values(ABILITY).map((a) => (
                  <span key={a.label} className="inline-flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: a.dot }} /> {a.label}
                  </span>
                ))}
              </div>
            </section>

            <section>
              <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Machines</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.machines.map((m) => (
                  <MachineCard key={m.id} m={m} />
                ))}
              </div>
            </section>

            <Card>
              <CardHeader
                title="Calendar"
                subtitle={`${data.calendar.events} entries: published videos, deadlines, published workflows and planned dates.`}
                action={
                  <Button size="sm" onClick={copy}>
                    <Copy size={13} /> Copy link
                  </Button>
                }
              />
              <div className="px-4 pb-3.5">
                <input
                  readOnly
                  value={feed}
                  aria-label="Calendar subscription link"
                  onFocus={(e) => e.target.select()}
                  className="h-8 w-full rounded-[var(--radius-sm)] border border-border-strong bg-surface-2 px-2 font-mono text-[12px] text-ink-2"
                />
                <p className="mt-1.5 text-[11.5px] text-ink-3">
                  Add it in your calendar app as a subscribed calendar. The app has to reach this address, so the device needs to be on your network or Tailscale.
                </p>
                {data.calendar.upcoming.length > 0 && (
                  <ul className="mt-3 space-y-1 text-[12.5px] text-ink-2">
                    {data.calendar.upcoming.map((e) => (
                      <li key={e.id} className="flex items-center gap-2">
                        <CalendarDays size={12} className="text-ink-3" />
                        <span className="w-[82px] shrink-0 text-ink-3">{e.date}</span>
                        <span className="truncate text-ink">{e.title}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Card>
          </div>
        )}
      </PageBody>
    </>
  )
}
