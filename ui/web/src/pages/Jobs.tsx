import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, File, Folder, Pin, RefreshCw, ChevronUp } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Drawer'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'

type Status = 'todo' | 'active' | 'done'
interface Stage {
  id: string
  label: string
  status: Status
  auto: Status
  manual: boolean
  files: number
  first: number | null
  last: number | null
  evidence: string[]
}
interface Job {
  id: string
  title: string
  start: string
  sponsored: boolean
  stages: Stage[]
  current: string
  last_activity: number | null
  idle_days: number | null
  archived: boolean
  archived_manual: boolean
  pinned: boolean
  deadline: string
  note: string
  publish_url: string
  script: string
  final_export: string
  thumbnails: string[]
  thumbnail: string
}
interface Activity {
  ts: number
  job: string
  who: string
  text: string
}
interface Board {
  root: string
  mounted: boolean
  stages: { id: string; label: string }[]
  jobs: Job[]
  activity: Activity[]
}
interface FileRow {
  name: string
  rel: string
  dir: boolean
  kind: string
}
interface Detail {
  job: Job
  rel: string
  files: FileRow[]
  activity: Activity[]
}

/** One colour per stage, the same everywhere on the page. */
const STAGE_COLOR: Record<string, string> = {
  research: 'var(--room-slate)',
  script: 'var(--room-indigo)',
  film: 'var(--room-violet)',
  edit: 'var(--room-teal)',
  packaging: 'var(--room-amber)',
  publish: 'var(--room-rose)',
  shorts: 'var(--room-lime)',
}
const DAY = 86400
const fileUrl = (job: string, rel: string) =>
  `/api/v1/jobs/file?job=${encodeURIComponent(job)}&rel=${encodeURIComponent(rel)}`
const dayLabel = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
const dateTs = (iso: string) => Math.floor(new Date(iso + 'T12:00:00').getTime() / 1000)

function Pips({ stages }: { stages: Stage[] }) {
  return (
    <div className="flex gap-1">
      {stages.map((s) => (
        <span
          key={s.id}
          title={`${s.label}: ${s.status}`}
          className="h-1.5 flex-1 rounded-full"
          style={{
            background: s.status === 'todo' ? 'var(--border-strong)' : STAGE_COLOR[s.id],
            opacity: s.status === 'active' ? 0.55 : 1,
          }}
        />
      ))}
    </div>
  )
}

function Gantt({ jobs, onOpen }: { jobs: Job[]; onOpen: (id: string) => void }) {
  const now = Math.floor(Date.now() / 1000)
  const { t0, t1, ticks } = useMemo(() => {
    let lo = now - 21 * DAY
    let hi = now + 10 * DAY
    for (const j of jobs) {
      lo = Math.min(lo, dateTs(j.start))
      if (j.deadline) hi = Math.max(hi, dateTs(j.deadline) + 3 * DAY)
    }
    lo = Math.max(lo, now - 120 * DAY)
    const tk: number[] = []
    const d = new Date(lo * 1000)
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() + ((8 - d.getDay()) % 7)) // next Monday
    for (let t = d.getTime() / 1000; t < hi; t += 7 * DAY) tk.push(t)
    return { t0: lo, t1: hi, ticks: tk }
  }, [jobs, now])
  const pct = (t: number) => Math.min(100, Math.max(0, ((t - t0) / (t1 - t0)) * 100))

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[720px]">
        <div className="relative ml-[210px] h-6 border-b border-border">
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute top-1 -translate-x-1/2 text-[10.5px] text-ink-3"
              style={{ left: `${pct(t)}%` }}
            >
              {dayLabel(t)}
            </span>
          ))}
        </div>
        {jobs.map((j) => (
          <button
            key={j.id}
            onClick={() => onOpen(j.id)}
            className="flex w-full items-center border-b border-border/60 text-left hover:bg-surface-hover"
          >
            <div className="w-[210px] shrink-0 truncate px-3 py-2 text-[12.5px] text-ink">
              {j.title}
              <div className="text-[10.5px] text-ink-3">
                {j.current === 'done' ? 'Done' : `At ${j.current}`}
                {j.idle_days ? ` · idle ${j.idle_days}d` : ''}
              </div>
            </div>
            <div className="relative h-9 flex-1">
              {ticks.map((t) => (
                <span
                  key={t}
                  className="absolute inset-y-0 w-px bg-border/60"
                  style={{ left: `${pct(t)}%` }}
                />
              ))}
              {j.stages
                .filter((s) => s.first && s.last)
                .map((s, i) => {
                  const a = pct(Math.max(s.first!, t0))
                  const b = pct(s.last! + DAY / 2)
                  return (
                    <span
                      key={s.id}
                      title={`${s.label}: ${dayLabel(s.first!)} to ${dayLabel(s.last!)} (${s.status})`}
                      className="absolute h-[5px] rounded-full"
                      style={{
                        left: `${a}%`,
                        width: `max(6px, ${b - a}%)`,
                        top: `${5 + i * 6}px`,
                        background: STAGE_COLOR[s.id],
                        opacity: s.status === 'done' ? 1 : 0.6,
                      }}
                    />
                  )
                })}
              {j.deadline && (
                <span
                  title={`Deadline ${j.deadline}`}
                  className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-danger"
                  style={{ left: `${pct(dateTs(j.deadline))}%` }}
                />
              )}
              <span className="absolute inset-y-0 w-px bg-accent" style={{ left: `${pct(now)}%` }} />
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function JobDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useToast()
  const qc = useQueryClient()
  const [rel, setRel] = useState('')
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null)
  const { data } = useQuery({
    queryKey: ['job', id, rel],
    queryFn: () =>
      api.get<Detail>(`/api/v1/jobs/detail?job=${encodeURIComponent(id)}&rel=${encodeURIComponent(rel)}`),
  })
  const job = data?.job

  async function save(changes: Record<string, unknown>) {
    try {
      await api.post('/api/v1/jobs/update', { job: id, ...changes })
      qc.invalidateQueries({ queryKey: ['job', id] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function openFile(f: FileRow) {
    if (f.dir) {
      setPreview(null)
      setRel(f.rel)
    } else if (f.kind === 'text') {
      try {
        setPreview(await api.get<{ name: string; text: string }>(fileUrl(id, f.rel)))
      } catch (e) {
        toast.error(String(e))
      }
    } else if (f.kind !== 'other') {
      window.open(fileUrl(id, f.rel), '_blank', 'noopener')
    }
  }

  const input =
    'h-8 w-full rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 text-[13px] text-ink'
  return (
    <Drawer open onClose={onClose} title={job?.title ?? id}>
      {!job ? (
        <p className="text-[13px] text-ink-3">Loading…</p>
      ) : (
        <div className="space-y-5">
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save({ pinned: !job.pinned })}>
              <Pin size={13} /> {job.pinned ? 'Unpin' : 'Pin'}
            </Button>
            <Button size="sm" onClick={() => save({ archived: !job.archived })}>
              {job.archived ? <ArchiveRestore size={13} /> : <Archive size={13} />}
              {job.archived ? 'Bring back' : 'Archive'}
            </Button>
            {job.archived_manual && (
              <Button size="sm" variant="ghost" onClick={() => save({ archived_auto: true })}>
                Automatic
              </Button>
            )}
          </div>

          <section>
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Stages</h3>
            <div className="space-y-1.5">
              {job.stages.map((s) => (
                <div key={s.id} className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: STAGE_COLOR[s.id] }} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] text-ink">{s.label}</div>
                    <div className="truncate text-[11px] text-ink-3">
                      {s.evidence.join(', ') || 'No files yet'}
                    </div>
                  </div>
                  <select
                    aria-label={`${s.label} status`}
                    className={cn(input, 'w-[120px]')}
                    value={s.manual ? s.status : 'auto'}
                    onChange={(e) => save({ stages: { [s.id]: e.target.value } })}
                  >
                    <option value="auto">Auto: {s.auto}</option>
                    <option value="todo">To do</option>
                    <option value="active">In progress</option>
                    <option value="done">Done</option>
                  </select>
                </div>
              ))}
            </div>
          </section>

          <section className="grid grid-cols-2 gap-3">
            <label className="text-[12px] text-ink-2">
              Deadline
              <input
                type="date"
                className={cn(input, 'mt-1')}
                defaultValue={job.deadline}
                onBlur={(e) => e.target.value !== job.deadline && save({ deadline: e.target.value })}
              />
            </label>
            <label className="text-[12px] text-ink-2">
              Published URL
              <input
                className={cn(input, 'mt-1')}
                placeholder="https://youtu.be/…"
                defaultValue={job.publish_url}
                onBlur={(e) => e.target.value !== job.publish_url && save({ publish_url: e.target.value })}
              />
            </label>
            <label className="col-span-2 text-[12px] text-ink-2">
              Note
              <textarea
                className={cn(input, 'mt-1 h-16 py-1.5')}
                defaultValue={job.note}
                onBlur={(e) => e.target.value !== job.note && save({ note: e.target.value })}
              />
            </label>
          </section>

          {job.thumbnails.length > 0 && (
            <section>
              <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">
                Thumbnails
              </h3>
              <div className="grid grid-cols-3 gap-2">
                {job.thumbnails.map((t) => (
                  <button
                    key={t}
                    title={`Use ${t} as the cover`}
                    onClick={() => save({ thumbnail: t })}
                    className={cn(
                      'overflow-hidden rounded-[var(--radius-sm)] border',
                      t === job.thumbnail ? 'border-accent' : 'border-border',
                    )}
                  >
                    <img src={fileUrl(id, t)} alt={t} loading="lazy" className="aspect-video w-full object-cover" />
                  </button>
                ))}
              </div>
            </section>
          )}

          <section>
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-[12px] font-semibold uppercase tracking-wide text-ink-3">
                Files /{data.rel}
              </h3>
              {data.rel && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label="Up one folder"
                  onClick={() => setRel(data.rel.split('/').slice(0, -1).join('/'))}
                >
                  <ChevronUp size={14} />
                </Button>
              )}
            </div>
            <div className="max-h-64 overflow-y-auto rounded-[var(--radius-sm)] border border-border">
              {data.files.map((f) => (
                <button
                  key={f.rel}
                  onClick={() => openFile(f)}
                  className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] text-ink hover:bg-surface-hover"
                >
                  {f.dir ? <Folder size={13} className="text-ink-3" /> : <File size={13} className="text-ink-3" />}
                  <span className="truncate">{f.name}</span>
                </button>
              ))}
            </div>
            {preview && (
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-[var(--radius-sm)] border border-border bg-surface-2 p-3 text-[12px] text-ink">
                {preview.text}
              </pre>
            )}
          </section>

          {data.activity.length > 0 && (
            <section>
              <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-ink-3">Work done</h3>
              <ul className="space-y-1 text-[12.5px] text-ink-2">
                {data.activity.map((a) => (
                  <li key={a.ts + a.text}>
                    <span className="text-ink-3">{dayLabel(a.ts)}</span> {a.text}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </Drawer>
  )
}

export function Jobs() {
  const [showArchived, setShowArchived] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<Board>('/api/v1/jobs'),
  })

  async function rescan() {
    setRefreshing(true)
    try {
      qc.setQueryData(['jobs'], await api.get<Board>('/api/v1/jobs?refresh=1'))
    } finally {
      setRefreshing(false)
    }
  }

  const all = data?.jobs ?? []
  const jobs = all.filter((j) => showArchived || !j.archived)
  const archivedCount = all.filter((j) => j.archived).length
  const active = all.filter((j) => !j.archived)

  return (
    <>
      <PageHead
        title="Jobs"
        subtitle={data ? `${active.length} active · ${archivedCount} archived` : undefined}
        actions={
          <>
            <Button size="sm" variant={showArchived ? 'primary' : 'default'} onClick={() => setShowArchived((v) => !v)}>
              <Archive size={13} /> {showArchived ? 'Hide archived' : 'Show archived'}
            </Button>
            <Button size="sm" onClick={rescan} disabled={refreshing}>
              <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /> Rescan
            </Button>
          </>
        }
      />
      <PageBody>
        {isLoading ? (
          <p className="text-[13px] text-ink-3">Reading the video folders…</p>
        ) : !data?.mounted ? (
          <EmptyState
            icon={Folder}
            title="The YouTube folder is not mounted"
            hint={`Nothing found at ${data?.root}. Mount the NAS folder there or set YOUTUBE_ROOT.`}
          />
        ) : (
          <div className="space-y-5">
            <div className="grid grid-cols-4 gap-2 md:grid-cols-7">
              {data.stages.map((s) => {
                const n = active.filter((j) => j.current === s.id).length
                return (
                  <Card key={s.id} className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
                      <span className="h-2 w-2 rounded-full" style={{ background: STAGE_COLOR[s.id] }} />
                      {s.label}
                    </div>
                    <div className="mt-0.5 text-[22px] font-bold leading-none text-ink">{n}</div>
                  </Card>
                )
              })}
            </div>

            <Card>
              <CardHeader title="Timeline" subtitle="One row per video. Bars show when each stage had files touched; the diamond is the deadline." />
              <Gantt jobs={jobs} onOpen={setOpen} />
            </Card>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {jobs.map((j) => (
                <Card
                  key={j.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setOpen(j.id)}
                  onKeyDown={(e) => e.key === 'Enter' && setOpen(j.id)}
                  className={cn('cursor-pointer overflow-hidden hover:border-border-strong', j.archived && 'opacity-60')}
                >
                  {j.thumbnail ? (
                    <img src={fileUrl(j.id, j.thumbnail)} alt="" loading="lazy" className="aspect-video w-full object-cover" />
                  ) : (
                    <div className="flex aspect-video w-full items-center justify-center bg-surface-2 text-[12px] text-ink-3">
                      No thumbnail yet
                    </div>
                  )}
                  <div className="space-y-2 px-3 py-2.5">
                    <div className="truncate text-[13px] font-semibold text-ink">{j.title}</div>
                    <Pips stages={j.stages} />
                    <div className="flex items-center justify-between text-[11.5px] text-ink-3">
                      <span>{j.current === 'done' ? 'Done' : `At ${j.current}`}</span>
                      <span className={j.deadline ? 'text-danger' : ''}>
                        {j.deadline ? `Due ${j.deadline}` : j.sponsored ? 'Sponsored' : j.start}
                      </span>
                    </div>
                  </div>
                </Card>
              ))}
            </div>

            {data.activity.length > 0 && (
              <Card>
                <CardHeader title="Work done" subtitle="Logged by sessions as deliverables land." />
                <ul className="space-y-1 px-4 pb-3.5 text-[12.5px] text-ink-2">
                  {data.activity.slice(0, 12).map((a) => (
                    <li key={a.ts + a.text}>
                      <span className="text-ink-3">{dayLabel(a.ts)}</span> {a.text}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        )}
      </PageBody>
      {open && <JobDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  )
}
