import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, FlaskConical, Plus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'

interface LabEntry {
  slug: string
  status: 'queued' | 'testing' | 'built' | 'failed' | 'waiting' | 'published' | 'skipped'
  pr: string
  note?: string
  updated: number
  log: { ts: number; text: string }[]
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
const STEPS = ['Found', 'Queued', 'Testing', 'Built', 'Published'] as const
const STEP_COLOR = ['var(--room-slate)', 'var(--room-indigo)', 'var(--room-violet)', 'var(--room-teal)', 'var(--room-lime)']
const STEP_OF: Record<string, number> = { queued: 1, testing: 2, failed: 2, waiting: 2, built: 3, published: 4 }
const STATUS_TEXT: Record<string, string> = {
  queued: 'Waiting for the lab',
  testing: 'Lab is working on it',
  built: 'Workflow built, waiting for your OK',
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

function Steps({ model }: { model: Model }) {
  const at = model.lab ? (STEP_OF[model.lab.status] ?? 0) : 0
  const bad = model.lab?.status === 'failed'
  return (
    <div className="flex min-w-0 flex-1">
      {STEPS.map((label, i) => (
        <div
          key={label}
          className="flex h-7 min-w-0 flex-1 items-center justify-center text-[11px] font-medium"
          style={{
            marginLeft: i ? -7 : 0,
            clipPath: i
              ? 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%, 9px 50%)'
              : 'polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%)',
            background:
              i === at
                ? bad
                  ? 'var(--danger)'
                  : STEP_COLOR[i]
                : i < at
                  ? `color-mix(in srgb, ${STEP_COLOR[i]} 30%, var(--surface))`
                  : 'var(--surface-hover)',
            color: i === at ? '#fff' : i < at ? 'var(--ink)' : 'var(--ink-3)',
          }}
        >
          <span className="truncate px-3">{i === at && bad ? 'Failed' : label}</span>
        </div>
      ))}
    </div>
  )
}

function Row({ model, onSet }: { model: Model; onSet: (slug: string, name: string, status: string) => void }) {
  const [open, setOpen] = useState(false)
  const lab = model.lab
  return (
    <div className="border-b border-border last:border-b-0">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <button className="w-[230px] shrink-0 text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <div className="truncate text-[13px] font-semibold text-ink">{model.name}</div>
          <div className="text-[11px] text-ink-3">
            {model.age_days <= 1 ? 'new today' : `found ${model.age_days}d ago`} · {model.stage || 'manual'} · hot {model.hot}
          </div>
        </button>
        <Steps model={model} />
        <div className="flex w-[104px] shrink-0 justify-end gap-1">
          {!lab || lab.status === 'skipped' || lab.status === 'failed' ? (
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
  const set = (slug: string, name: string, status: string) => post('/api/v1/models/set', { slug, name, status })

  const all = data?.models ?? []
  const inLab = all
    .filter((m) => m.lab && m.lab.status !== 'skipped')
    .sort((a, b) => (STEP_OF[a.lab!.status] === 2 ? -1 : 0) - (STEP_OF[b.lab!.status] === 2 ? -1 : 0) || b.lab!.updated - a.lab!.updated)
  const fresh = all
    .filter((m) => !m.lab && !m.covered && (showAll || (m.released && m.age_days <= 45)))
    .sort((a, b) => a.age_days - b.age_days || b.hot - a.hot)
  const hb = data?.heartbeat
  const alive = hb?.ts && Date.now() / 1000 - hb.ts < 36 * 3600

  return (
    <>
      <PageHead
        title="Workflow Maker"
        subtitle="New models the radar found, and what the lab is doing about each."
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
            <CardHeader title={`In the lab (${inLab.length})`} subtitle="Click a model for its progress log." />
            {inLab.length === 0 && <p className="px-4 pb-4 text-[12.5px] text-ink-3">Nothing queued. Pick models below or press “Test all new models”.</p>}
            {inLab.map((m) => (
              <Row key={m.slug} model={m} onSet={set} />
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
              <Row key={m.slug} model={m} onSet={set} />
            ))}
          </Card>
        </div>
      </PageBody>
    </>
  )
}
