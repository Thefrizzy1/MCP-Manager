import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, ChevronRight, File, Folder, Pin, RefreshCw, ChevronUp } from 'lucide-react'
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
  youtube_thumbnail: string
  published: { id: string; title: string; date: string; views: number | null } | null
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
  test: 'var(--room-teal)',
  film: 'var(--room-violet)',
  edit: 'var(--room-rose)',
  packaging: 'var(--room-amber)',
  publish: 'var(--room-lime)',
  shorts: 'var(--ok)',
}
const fileUrl = (job: string, rel: string) =>
  `/api/v1/jobs/file?job=${encodeURIComponent(job)}&rel=${encodeURIComponent(rel)}`
const dayLabel = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })

/** The pipeline as arrows: finished stages tinted, the current one solid, the rest empty. */
function Stepper({ job }: { job: Job }) {
  return (
    <div className="flex min-w-0 flex-1" role="list" aria-label={`Stages of ${job.title}`}>
      {job.stages.map((s, i) => {
        const current = s.id === job.current
        const color = STAGE_COLOR[s.id]
        const first = i === 0
        return (
          <div
            key={s.id}
            role="listitem"
            title={`${s.label}: ${s.status === 'done' ? 'done' : current ? 'working on it' : s.status === 'active' ? 'started' : 'not started'}`}
            className="relative flex h-7 min-w-0 flex-1 items-center justify-center text-[11px] font-medium"
            style={{
              marginLeft: first ? 0 : -7,
              clipPath: first
                ? 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%)'
                : 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%, 9px 50%)',
              background: current
                ? color
                : s.status === 'done'
                  ? `color-mix(in srgb, ${color} 30%, var(--surface))`
                  : s.status === 'active'
                    ? `color-mix(in srgb, ${color} 14%, var(--surface-2))`
                    : 'var(--surface-hover)',
              color: current ? '#fff' : s.status === 'todo' ? 'var(--ink-3)' : 'var(--ink)',
            }}
          >
            <span className="truncate px-3">
              {s.status === 'done' && !current ? '✓ ' : ''}
              {s.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function JobRow({ job, onDetails }: { job: Job; onDetails: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-border last:border-b-0">
      <button
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-surface-hover"
      >
        <ChevronRight size={14} className={cn('shrink-0 text-ink-3 transition-transform', open && 'rotate-90')} />
        {job.youtube_thumbnail || job.thumbnail ? (
          <img
            src={job.youtube_thumbnail || fileUrl(job.id, job.thumbnail)}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-9 w-16 shrink-0 rounded-[4px] object-cover"
          />
        ) : (
          <div className="h-9 w-16 shrink-0 rounded-[4px] bg-surface-hover" />
        )}
        <div className="w-[200px] shrink-0">
          <div className="truncate text-[13px] font-semibold text-ink">{job.title}</div>
          {job.deadline && <div className="text-[11px] text-danger">due {job.deadline.slice(5)}</div>}
        </div>
        <Stepper job={job} />
      </button>
      {open && (
        <div className="border-t border-border bg-surface-2 px-4 py-3 pl-[52px]">
          <div className="space-y-1.5 text-[12px] text-ink-2">
            <div>Started {job.start}{job.idle_days ? ` · last touched ${job.idle_days}d ago` : ' · touched today'}</div>
            {job.published && (
              <div>
                Live since {job.published.date || 'recently'}:{' '}
                <a className="text-accent hover:underline" href={job.publish_url} target="_blank" rel="noreferrer">
                  {job.published.title}
                </a>
                {job.published.views != null && ` · ${job.published.views.toLocaleString()} views`}
              </div>
            )}
            {job.note && <div className="text-ink">{job.note}</div>}
            <Button size="sm" onClick={() => onDetails(job.id)}>
              Files, thumbnails and settings
            </Button>
          </div>
        </div>
      )}
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
  const archived = all.filter((j) => j.archived)
  const active = all.filter((j) => !j.archived)

  return (
    <>
      <PageHead
        title="YouTube Studio"
                actions={
          <>
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
            <Card>
              <CardHeader title="In progress" />
              {active.length === 0 ? (
                <p className="px-4 pb-4 text-[12.5px] text-ink-3">Nothing in progress.</p>
              ) : (
                active.map((j) => <JobRow key={j.id} job={j} onDetails={setOpen} />)
              )}
            </Card>

            <Card>
              <button
                aria-expanded={showArchived}
                onClick={() => setShowArchived((v) => !v)}
                className="flex w-full items-center gap-2 px-4 py-3 text-left"
              >
                <ChevronRight size={14} className={cn('text-ink-3 transition-transform', showArchived && 'rotate-90')} />
                <Archive size={14} className="text-ink-3" />
                <span className="text-[13px] font-semibold text-ink">Archive</span>
                <span className="text-[12px] text-ink-3">{archived.length}</span>
              </button>
              {showArchived && archived.map((j) => <JobRow key={j.id} job={j} onDetails={setOpen} />)}
            </Card>

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
