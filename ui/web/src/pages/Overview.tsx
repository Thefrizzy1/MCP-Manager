import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, Copy } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Drawer'
import { useToast } from '@/components/ui/Toast'
import { PixelLaptop, PixelRobot, PixelServer, WORKER_RAMP } from '@/components/PixelArt'
import { cn } from '@/lib/cn'

interface Schedule {
  enabled: boolean
  start: string
  end: string
  days: number[]
  may_wake: boolean
  may_power_off: boolean
}
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
  schedule?: Schedule
  form?: 'server' | 'laptop'
  specs?: {
    cores?: number
    ram_gb?: number
    gpu?: string
    vram_gb?: number
    vram_used_gb?: number
    gpu_util?: number
    disk_free_gb?: number
    disk_total_gb?: number
  }
}
interface Worker {
  id: string
  label: string
  about: string
  home: string
  model: string
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
}
interface Data {
  machines: Machine[]
  workers: Worker[]
  models: string[]
  calendar: { path: string; events: number; upcoming: Event[] }
}

const ABILITY: Record<string, { dot: string; label: string }> = {
  running: { dot: 'var(--ok)', label: 'runs by itself' },
  ready: { dot: 'var(--accent)', label: 'on request' },
  planned: { dot: 'var(--border-strong)', label: 'not built yet' },
}
const DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
const COL = 250

function ago(ts?: number) {
  if (!ts) return 'never'
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`
}
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

/** The picture: every machine as a little server, its workers standing below it. */
function FleetMap({ data, onMachine, onWorker }: { data: Data; onMachine: (id: string) => void; onWorker: (id: string) => void }) {
  const ids = new Set(data.machines.map((m) => m.id))
  const homeOf = (w: Worker) => (ids.has(w.home) ? w.home : data.machines[0]?.id)
  const width = Math.max(1, data.machines.length) * COL
  return (
    <svg viewBox={`0 0 ${width} 300`} width="100%" style={{ maxWidth: width * 1.25 }} role="img" aria-label="Machines and the workers living on them">
      {data.machines.map((m, i) => {
        const cx = i * COL + COL / 2
        const working = m.online && !!m.doing
        const team = data.workers.filter((w) => homeOf(w) === m.id)
        const s = m.specs ?? {}
        const line = !m.online
          ? m.seen
            ? `${m.form === 'laptop' ? 'closed' : 'off'} · last seen ${ago(m.seen)}`
            : 'off · not connected yet'
          : working
            ? clip(m.doing!, 32)
            : [s.gpu && s.vram_gb ? `${s.vram_gb} GB GPU` : s.gpu ? s.gpu : s.cores ? `${s.cores} cores` : '', s.disk_free_gb != null ? `${s.disk_free_gb} GB free` : ''].filter(Boolean).join(' · ') || 'idle'
        return (
          <g key={m.id}>
            <g role="button" tabIndex={0} className="cursor-pointer" onClick={() => onMachine(m.id)} onKeyDown={(e) => e.key === 'Enter' && onMachine(m.id)}>
              <rect x={i * COL + 8} y={4} width={COL - 16} height={150} rx={10} fill="transparent" />
              {m.form === 'laptop' ? (
                <PixelLaptop x={cx - 48} y={22} on={m.online} busy={working} />
              ) : (
                <PixelServer x={cx - 48} y={22} on={m.online} busy={working} fans={s.gpu ? 2 : 1} />
              )}
              <text x={cx} y={118} textAnchor="middle" style={{ fill: 'var(--ink)', fontSize: 14, fontWeight: 600 }}>
                {clip(m.name, 26)}
              </text>
              <text x={cx} y={136} textAnchor="middle" style={{ fill: working ? 'var(--room-violet)' : 'var(--ink-3)', fontSize: 11.5 }}>
                {line}
              </text>
            </g>
            {team.map((w, k) => {
              const rx = cx + (k - (team.length - 1) / 2) * (team.length > 3 ? 62 : 78)
              return (
                <g key={w.id} role="button" tabIndex={0} className="cursor-pointer" onClick={() => onWorker(w.id)} onKeyDown={(e) => e.key === 'Enter' && onWorker(w.id)}>
                  <line x1={rx} y1={146} x2={rx} y2={164} stroke="var(--border-strong)" strokeDasharray="2 3" />
                  <rect x={rx - 38} y={164} width={76} height={120} rx={8} fill="transparent" />
                  <PixelRobot kind={w.id} x={rx - 30} y={170} px={5} active={w.active} busy={w.busy} />
                  <text x={rx} y={248} textAnchor="middle" style={{ fill: w.active ? 'var(--ink)' : 'var(--ink-3)', fontSize: 12, fontWeight: 600 }}>
                    {w.label.replace(' Manager', ' Mgr').replace('Workflow Maker', 'Maker')}
                  </text>
                  <text x={rx} y={264} textAnchor="middle" style={{ fill: w.busy ? (WORKER_RAMP[w.id]?.[0] ?? 'var(--accent)') : 'var(--ink-3)', fontSize: 11 }}>
                    {!w.active ? 'not set up' : w.busy ? 'working' : 'idle'}
                  </text>
                </g>
              )
            })}
          </g>
        )
      })}
    </svg>
  )
}

function WorkerDetail({ w, data, post }: { w: Worker; data: Data; post: (path: string, body: unknown) => void }) {
  const select = 'h-8 rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 text-[13px] text-ink'
  return (
    <div className="space-y-4 text-[13px] text-ink-2">
      <p>{w.about}</p>
      {w.text && (
        <div className="rounded-[var(--radius-sm)] bg-surface-2 px-3 py-2 text-ink">
          {w.text}
          <div className="text-[11.5px] text-ink-3">
            {w.busy ? 'working now' : `last worked ${ago(w.ts)}`}
            {w.next ? ` · next: ${w.next}` : ''}
          </div>
        </div>
      )}
      <ul className="space-y-1.5">
        {w.abilities.map((a) => (
          <li key={a.text} className="flex items-start gap-2">
            <span className="mt-[6px] h-2 w-2 shrink-0 rounded-full" style={{ background: ABILITY[a.state].dot }} />
            <span className={a.state === 'planned' ? 'text-ink-3' : 'text-ink'}>
              {a.text} <span className="text-[11.5px] text-ink-3">· {ABILITY[a.state].label}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-[12px]">
          Model
          <select className={cn(select, 'mt-1 w-full')} value={w.model} onChange={(e) => post('/api/v1/overview/worker', { worker: w.id, model: e.target.value })}>
            {data.models.map((m) => (
              <option key={m} value={m}>
                {m[0].toUpperCase() + m.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[12px]">
          Lives on
          <select className={cn(select, 'mt-1 w-full')} value={w.home} onChange={(e) => post('/api/v1/overview/worker', { worker: w.id, home: e.target.value })}>
            {data.machines.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}

function MachineDetail({ m, post }: { m: Machine; post: (path: string, body: unknown) => void }) {
  const s = m.specs ?? {}
  const [sch, setSch] = useState<Schedule>(m.schedule ?? { enabled: true, start: '03:00', end: '06:00', days: [0, 1, 2, 3, 4, 5, 6], may_wake: true, may_power_off: true })
  const input = 'h-8 rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 text-[13px] text-ink'
  const facts = [
    s.cores ? `${s.cores} cores` : '',
    s.ram_gb ? `${s.ram_gb} GB memory` : '',
    s.gpu ? `${s.gpu}${s.vram_gb ? `, ${s.vram_used_gb ?? 0} of ${s.vram_gb} GB in use` : ''}${s.gpu_util != null ? `, ${s.gpu_util}% busy` : ''}` : '',
    s.disk_total_gb ? `${s.disk_free_gb} GB free of ${s.disk_total_gb}` : '',
  ].filter(Boolean)
  return (
    <div className="space-y-4 text-[13px] text-ink-2">
      {m.role && <p>{m.role}</p>}
      <div className="rounded-[var(--radius-sm)] bg-surface-2 px-3 py-2">
        <div className="text-ink">{m.online ? m.doing || 'On, idle' : m.seen ? `Off, last seen ${ago(m.seen)}` : 'Off. No node is installed on it yet, so it cannot report.'}</div>
        {m.next && <div className="text-[11.5px] text-ink-3">Next: {m.next}</div>}
      </div>
      {facts.length > 0 && (
        <ul className="space-y-0.5">
          {facts.map((f) => (
            <li key={f}>· {f}</li>
          ))}
        </ul>
      )}
      {m.can && m.can.length > 0 && (
        <div>
          <div className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Can do</div>
          <ul className="space-y-0.5 text-ink">
            {m.can.map((c) => (
              <li key={c}>· {c}</li>
            ))}
          </ul>
        </div>
      )}
      {!m.here && (
        <div className="border-t border-border pt-3">
          <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Work hours</div>
          <label className="mb-2 flex items-center gap-2 text-ink">
            <input type="checkbox" checked={sch.enabled} onChange={(e) => setSch({ ...sch, enabled: e.target.checked })} />
            May work by itself during these hours
          </label>
          <div className="flex items-center gap-2">
            <input type="time" className={input} value={sch.start} aria-label="From" onChange={(e) => setSch({ ...sch, start: e.target.value })} />
            <span>to</span>
            <input type="time" className={input} value={sch.end} aria-label="Until" onChange={(e) => setSch({ ...sch, end: e.target.value })} />
          </div>
          <div className="mt-2 flex gap-1">
            {DAYS.map((d, i) => {
              const on = sch.days.includes(i)
              return (
                <button
                  key={d}
                  aria-pressed={on}
                  onClick={() => setSch({ ...sch, days: on ? sch.days.filter((x) => x !== i) : [...sch.days, i].sort() })}
                  className={cn('h-7 w-9 rounded-[var(--radius-sm)] border text-[12px]', on ? 'border-accent bg-accent text-accent-fg' : 'border-border text-ink-3')}
                >
                  {d}
                </button>
              )
            })}
          </div>
          <label className="mt-3 flex items-center gap-2 text-ink">
            <input type="checkbox" checked={sch.may_wake} onChange={(e) => setSch({ ...sch, may_wake: e.target.checked })} />
            Switch it on at the start if there is work
          </label>
          <label className="mt-1 flex items-center gap-2 text-ink">
            <input type="checkbox" checked={sch.may_power_off} onChange={(e) => setSch({ ...sch, may_power_off: e.target.checked })} />
            Switch it off again when the work is done
          </label>
          <Button className="mt-3" variant="primary" size="sm" onClick={() => post('/api/v1/overview/schedule', { machine: m.id, ...sch })}>
            Save work hours
          </Button>
        </div>
      )}
    </div>
  )
}

export function Overview() {
  const toast = useToast()
  const qc = useQueryClient()
  const [open, setOpen] = useState<{ kind: 'machine' | 'worker'; id: string } | null>(null)
  const { data } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Data>('/api/v1/overview'),
    refetchInterval: 20_000,
  })
  const feed = data ? `${window.location.origin}${data.calendar.path}` : ''

  async function post(path: string, body: unknown) {
    try {
      qc.setQueryData(['overview'], await api.post<Data>(path, body))
      toast.success('Saved.')
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(feed)
      toast.success('Calendar link copied.')
    } catch {
      toast.error('Could not copy. Select the link and copy it by hand.')
    }
  }
  const machine = open?.kind === 'machine' ? data?.machines.find((m) => m.id === open.id) : undefined
  const worker = open?.kind === 'worker' ? data?.workers.find((w) => w.id === open.id) : undefined

  return (
    <>
      <PageHead title="Overview" />
      <PageBody>
        {!data ? (
          <p className="text-[13px] text-ink-3">Loading…</p>
        ) : (
          <div className="space-y-5">
            <Card className="flex justify-center px-2 py-4">
              <FleetMap data={data} onMachine={(id) => setOpen({ kind: 'machine', id })} onWorker={(id) => setOpen({ kind: 'worker', id })} />
            </Card>

            <Card>
              <CardHeader
                title="Calendar"
                subtitle={`${data.calendar.events} entries`}
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
      {machine && (
        <Drawer open onClose={() => setOpen(null)} title={machine.name}>
          <MachineDetail key={machine.id} m={machine} post={post} />
        </Drawer>
      )}
      {worker && data && (
        <Drawer open onClose={() => setOpen(null)} title={worker.label}>
          <WorkerDetail w={worker} data={data} post={post} />
        </Drawer>
      )}
    </>
  )
}
