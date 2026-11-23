import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpToLine, Clapperboard, ExternalLink, FlaskConical, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'
import { navigate } from '@/lib/router'

interface LabEntry {
  slug: string
  status: 'queued' | 'testing' | 'built' | 'failed' | 'waiting' | 'published' | 'skipped'
  pr: string
  note?: string
  added?: number
  updated: number
  log: { ts: number; text: string }[]
  publish_requested?: boolean
  video?: string
  update_pr?: string
  gpu3060?: { status: 'queued' | 'testing' | 'done' | 'failed'; text: string; ts: number }
  server?: { status: 'queued' | 'testing' | 'done' | 'failed'; text: string; ts: number }
  review?: {
    repo: string
    branch: string
    folder: string
    samples: string[]
    title?: string
    tested?: string
    summary?: string
    critique?: string
    not_verified?: string
  }
}
interface Model {
  slug: string
  name: string
  hot: number
  stage: string
  released: boolean
  covered: boolean
  age_days: number
  signals: number
  lab: LabEntry | null
}
interface Data {
  models: Model[]
  heartbeat: { ts?: number; text?: string }
}

/** The lab's steps for one model, as arrows like the Videos page. */
const STEPS = ['Found', 'Queued', 'Test', 'Published'] as const
const STEP_COLOR = ['var(--room-slate)', 'var(--room-indigo)', 'var(--room-violet)', 'var(--room-lime)']
// 'built' means at least one test passed: publishing is open while the other half is still to come.
const STEP_OF: Record<string, number> = { queued: 1, testing: 2, failed: 2, waiting: 2, built: 3, published: 3 }
const STATUS_TEXT: Record<string, string> = {
  queued: 'Waiting for the lab',
  testing: 'Lab is working on it',
  built: 'Server test passed. Open it to check, then press Publish',
  failed: 'Failed, see the log',
  waiting: 'Waiting for ComfyUI support or an input',
  published: 'Workflow published',
  skipped: 'Skipped',
}

function ago(ts?: number) {
  if (!ts) return 'never'
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`
}

type TestState = 'queued' | 'testing' | 'done' | 'failed' | undefined
const ARROW_FIRST = 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%)'
const ARROW = 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%, 9px 50%)'

/** One half of the Test arrow: the same test, on one of the two machines. */
function TestHalf({ label, state, note }: { label: string; state: TestState; note?: string }) {
  const color = STEP_COLOR[2]
  return (
    <div
      title={note || (state ? undefined : `${label}: not run yet`)}
      className={cn('flex min-h-0 w-full flex-1 items-center justify-center text-[9.5px] leading-none', state === 'testing' && 'stage-live')}
      style={{
        background:
          state === 'testing'
            ? color
            : state === 'done'
              ? `color-mix(in srgb, ${color} 30%, var(--surface))`
              : state === 'failed'
                ? 'var(--danger)'
                : 'var(--surface-hover)',
        color: state === 'testing' || state === 'failed' ? '#fff' : state === 'done' ? 'var(--ink)' : 'var(--ink-3)',
      }}
    >
      <span className="truncate px-1.5">
        {state === 'done' ? '✓ ' : ''}
        {label}
      </span>
    </div>
  )
}

function Steps({ model, onPublish }: { model: Model; onPublish: () => void }) {
  const lab = model.lab
  const at = lab ? (STEP_OF[lab.status] ?? 0) : 0
  const passed = lab?.status === 'built' || lab?.status === 'published'
  // Either machine may test first. Without a separate server report, the model's
  // own status is the server's (that is where the lab runs).
  const server: TestState =
    lab?.server?.status ??
    (lab?.gpu3060
      ? undefined
      : lab?.status === 'testing'
        ? 'testing'
        : lab?.status === 'failed'
          ? 'failed'
          : passed
            ? 'done'
            : undefined)
  const update = lab?.status === 'published' && !!lab.update_pr
  const ready = (lab?.status === 'built' && !!lab.pr) || update
  const asked = !!lab?.publish_requested
  return (
    <div className="flex min-w-0 flex-1">
      {STEPS.map((label, i) => {
        const base = 'flex h-8 min-w-0 items-center justify-center text-[11px] font-medium'
        const shape = { marginLeft: i ? -7 : 0, clipPath: i ? ARROW : ARROW_FIRST }
        if (i === 2)
          return (
            // One arrow, split top and bottom: the server test above, the 3060 test below.
            <div key={label} className={cn(base, 'flex-1 flex-col gap-px')} style={{ ...shape, background: 'var(--border)' }}>
              <TestHalf label="Server test" state={server} note={lab?.server?.text} />
              <TestHalf label="3060 test" state={lab?.gpu3060?.status} note={lab?.gpu3060?.text} />
            </div>
          )
        if (i === 3 && ready)
          return (
            <button
              key={label}
              disabled={asked}
              onClick={onPublish}
              title="Publish this workflow to GitHub and Hugging Face"
              className={cn(base, 'flex-1 font-semibold text-white hover:brightness-110 disabled:opacity-70')}
              style={{ ...shape, background: 'var(--accent)' }}
            >
              <span className="truncate px-3">{asked ? 'Publishing…' : update ? 'Update' : 'Publish'}</span>
            </button>
          )
        const done = i < at || (i === 3 && lab?.status === 'published')
        const current = i === at && i < 2
        return (
          <div
            key={label}
            className={cn(base, 'flex-1')}
            style={{
              ...shape,
              background: current
                ? STEP_COLOR[i]
                : done
                  ? `color-mix(in srgb, ${STEP_COLOR[i]} 30%, var(--surface))`
                  : 'var(--surface-hover)',
              color: current ? '#fff' : done ? 'var(--ink)' : 'var(--ink-3)',
            }}
          >
            <span className="truncate px-3">
              {done ? '✓ ' : ''}
              {label}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/** What to look at before pressing Publish: the graph, the outputs, the test notes. */
function Review({ review, pr }: { review: NonNullable<LabEntry['review']>; pr: string }) {
  const raw = (file: string) =>
    `https://raw.githubusercontent.com/${review.repo}/${encodeURIComponent(review.branch).replace(/%2F/g, '/')}/${review.folder}/samples/${encodeURIComponent(file)}`
  const page = `https://github.com/${review.repo}/tree/${review.branch}/${review.folder}`
  const graph = review.samples.find((f) => f.startsWith('workflow'))
  const outputs = review.samples.filter((f) => f !== graph)
  return (
    <div className="mb-3 space-y-2.5">
      {review.title && <div className="text-[13px] font-semibold text-ink">{review.title}</div>}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {graph && (
          <a href={raw(graph)} target="_blank" rel="noreferrer" className="col-span-2">
            <img src={raw(graph)} alt="Workflow graph" loading="lazy" className="w-full rounded-[var(--radius-sm)] border border-border" />
          </a>
        )}
        {outputs.map((f) =>
          /\.(mp4|webm)$/i.test(f) ? (
            <video key={f} src={raw(f)} controls muted className="w-full rounded-[var(--radius-sm)] border border-border" />
          ) : (
            <a key={f} href={raw(f)} target="_blank" rel="noreferrer">
              <img src={raw(f)} alt={f} loading="lazy" className="w-full rounded-[var(--radius-sm)] border border-border" />
            </a>
          ),
        )}
      </div>
      {review.summary && <div>{review.summary}</div>}
      {review.tested && (
        <div>
          <span className="text-ink">Tested:</span> {review.tested}
        </div>
      )}
      {review.critique && (
        <div>
          <span className="text-ink">Critique:</span> {review.critique}
        </div>
      )}
      {review.not_verified && (
        <div className="text-warn">
          Not verified: {review.not_verified}
        </div>
      )}
      <div className="flex gap-3">
        <a className="text-accent hover:underline" href={page} target="_blank" rel="noreferrer">
          Workflow page
        </a>
        {pr && (
          <a className="text-accent hover:underline" href={pr} target="_blank" rel="noreferrer">
            Pull request
          </a>
        )}
      </div>
    </div>
  )
}

function Row({
  model,
  onSet,
  onPublish,
  onVideo,
}: {
  model: Model
  onSet: (slug: string, name: string, status: string) => void
  onPublish: (slug: string) => void
  onVideo: (slug: string) => void
}) {
  const [open, setOpen] = useState(false)
  const lab = model.lab
  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <button className="w-[230px] shrink-0 text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <div className="truncate text-[13px] font-semibold text-ink">{model.name}</div>
          <div className="text-[11px] text-ink-3">
            {model.lab?.status === 'testing'
              ? 'working on it now'
              : model.lab
                ? STATUS_TEXT[model.lab.status].toLowerCase()
                : model.age_days <= 1
                  ? 'new today'
                  : `found ${model.age_days}d ago`}
          </div>
        </button>
        <Steps model={model} onPublish={() => onPublish(model.slug)} />
        <div className="flex w-[214px] shrink-0 justify-end gap-1">
          {lab && (lab.status === 'built' || lab.status === 'published') && (
            <Button size="sm" variant={lab.video ? 'default' : 'primary'} onClick={() => onVideo(model.slug)}>
              <Clapperboard size={13} /> {lab.video ? 'Open video' : 'Make a video'}
            </Button>
          )}
          {!lab || lab.status === 'skipped' || lab.status === 'failed' || lab.status === 'waiting' ? (
            <Button size="sm" onClick={() => onSet(model.slug, model.name, 'queued')}>
              <Plus size={13} /> Test
            </Button>
          ) : lab.pr ? (
            <a href={lab.pr} target="_blank" rel="noreferrer">
              <Button size="sm">
                <ExternalLink size={13} /> PR
              </Button>
            </a>
          ) : null}
          {lab?.status === 'queued' && (
            <Button size="sm" variant="ghost" title="Test this one next" onClick={() => onSet(model.slug, model.name, 'front')}>
              <ArrowUpToLine size={13} /> Next
            </Button>
          )}
          {(!lab || lab.status === 'queued') && (
            <Button size="icon-sm" variant="ghost" aria-label={`Skip ${model.name}`} onClick={() => onSet(model.slug, model.name, 'skipped')}>
              <X size={14} />
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div className="border-t border-border bg-surface-2 px-4 py-3 text-[12.5px] text-ink-2">
          {lab ? (
            <>
              <div className="mb-1.5 text-ink">{STATUS_TEXT[lab.status]} · updated {ago(lab.updated)}</div>
              {lab.review && <Review review={lab.review} pr={lab.pr} />}
              {lab.log.length === 0 && <div className="text-ink-3">No progress reported yet.</div>}
              <ul className="space-y-0.5">
                {[...lab.log].reverse().map((l) => (
                  <li key={l.ts + l.text}>
                    <span className="text-ink-3">{ago(l.ts)}</span> {l.text}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <div>
              Not on the lab's list. {model.released ? 'Files are out, so it can be tested.' : 'No downloadable files yet.'} {model.signals} signals on the radar.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function Models() {
  const toast = useToast()
  const qc = useQueryClient()
  const [showAll, setShowAll] = useState(false)
  const { data } = useQuery({
    queryKey: ['models'],
    queryFn: () => api.get<Data>('/api/v1/models'),
    refetchInterval: 20_000,
  })

  async function post(path: string, body: unknown) {
    try {
      qc.setQueryData(['models'], await api.post<Data>(path, body))
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function toVideo(slug: string) {
    try {
      const r = await api.post<{ job: string; created: boolean }>('/api/v1/models/to_video', { slug })
      if (r.created) toast.success('Video created. The Channel Manager is setting it up.')
      qc.invalidateQueries({ queryKey: ['models'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      navigate(`chat?job=${encodeURIComponent(r.job)}`)
    } catch (e) {
      toast.error(String(e))
    }
  }
  const set = (slug: string, name: string, status: string) => post('/api/v1/models/set', { slug, name, status })

  const all = data?.models ?? []
  const inLab = all
    .filter((m) => m.lab && m.lab.status !== 'skipped')
    .sort((a, b) => {
      // testing first, then the queue in the order the lab will take it, then the rest by recency
      const rank = (s: string) => (s === 'testing' ? 0 : s === 'queued' ? 1 : 2)
      const ra = rank(a.lab!.status), rb = rank(b.lab!.status)
      if (ra !== rb) return ra - rb
      if (ra === 1) return (a.lab!.added ?? 0) - (b.lab!.added ?? 0)
      return b.lab!.updated - a.lab!.updated
    })
  const fresh = all
    .filter((m) => !m.lab && !m.covered && (showAll || (m.released && m.age_days <= 45)))
    .sort((a, b) => a.age_days - b.age_days || b.hot - a.hot)
  const hb = data?.heartbeat
  const alive = hb?.ts && Date.now() / 1000 - hb.ts < 36 * 3600

  return (
    <>
      <PageHead
        title="Workflow Maker"
        actions={
          <Button size="sm" variant="primary" onClick={() => post('/api/v1/models/queue_new', { max_age_days: 45 })}>
            <FlaskConical size={13} /> Test all new models
          </Button>
        }
      />
      <PageBody>
        <div className="space-y-5">
          <Card className="flex items-center gap-3 px-4 py-3">
            <span className={cn('h-2.5 w-2.5 rounded-full', alive ? 'bg-ok' : 'bg-warn')} />
            <div className="text-[13px] text-ink">
              {hb?.ts ? `Lab last reported ${ago(hb.ts)}` : 'The lab has not reported yet'}
              <span className="text-ink-3">{hb?.text ? ` · ${hb.text}` : hb?.ts ? '' : ' · it is not installed on the server, or has not run'}</span>
            </div>
          </Card>

          <Card>
            <CardHeader title={`In the lab (${inLab.length})`} />
            {inLab.length === 0 && <p className="px-4 pb-4 text-[12.5px] text-ink-3">Nothing queued. Pick models below or press “Test all new models”.</p>}
            {inLab.map((m) => (
              <Row key={m.slug} model={m} onSet={set} onPublish={(slug) => post('/api/v1/models/publish', { slug })} onVideo={toVideo} />
            ))}
          </Card>

          <Card>
            <CardHeader
              title={`${showAll ? 'All models on the radar' : 'New models'} (${fresh.length})`}
              subtitle={showAll ? 'Everything not yet covered by a video.' : 'Released in the last 45 days, no video and no workflow from you yet.'}
              action={
                <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
                  {showAll ? 'Only new' : 'Show all'}
                </Button>
              }
            />
            {fresh.length === 0 && <p className="px-4 pb-4 text-[12.5px] text-ink-3">Nothing new right now.</p>}
            {fresh.map((m) => (
              <Row key={m.slug} model={m} onSet={set} onPublish={(slug) => post('/api/v1/models/publish', { slug })} onVideo={toVideo} />
            ))}
          </Card>
        </div>
      </PageBody>
    </>
  )
}
