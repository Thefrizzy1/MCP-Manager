import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clapperboard, Download, Film, Mic, Moon, Package, Plus, Power, RefreshCw, Trash2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Textarea } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { Switch } from '@/components/ui/Switch'
import { useToast } from '@/components/ui/Toast'
import { Section } from './bits'
import { ago } from './types'

interface Scene {
  id: string
  chapter: string
  speaker: number
  narration: string
  direction?: string
  visual: { type: string; heading?: string; lines?: string[]; code?: string; image?: string; clip?: string; caption?: string; bench_ids?: number[] }
  audio?: { file: string; seconds: number }
}
interface Prod {
  id: number
  idea_id: number | null
  title: string
  status: string
  auto: number
  seconds: number
  voiced: number
  scene_count?: number
  scenes?: Scene[]
  script_md?: string
  reference?: { url?: string; title?: string; chapter_count?: number; avg_chapter_seconds?: number; wpm?: number; error?: string }
  render?: { state?: string; error?: string; log?: string[] }
  updated: number
}
interface JobView {
  running: boolean
  log?: string[]
  error?: string | null
  current?: string | null
}
interface ListResp {
  productions: Prod[]
  jobs: Record<string, JobView>
  render: { ok: boolean; note: string; browser: string }
  comfy_up: boolean
  voice_ready: boolean
}
interface Detail {
  production: Prod
  voice_script: string
  bench: Record<string, { display: string; time: string; status: string }>
  has_video: boolean
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

function JobLine({ name, job }: { name: string; job?: JobView }) {
  if (!job || (!job.running && !job.error && !(job.log ?? []).length)) return null
  return (
    <div className={cn('rounded-md border px-3 py-2 text-[11.5px]', job.error ? 'border-danger/40 bg-danger-weak text-danger' : 'border-border bg-surface-2 text-ink-2')}>
      <b className="mr-1 capitalize">{name}</b>
      {job.running ? `running${job.current ? ` · ${job.current}` : ''}` : job.error ? job.error : 'done'}
      {(job.log ?? []).length > 0 && <div className="mt-1 max-h-24 overflow-auto font-mono text-[10.5px] text-ink-3">{(job.log ?? []).slice(-6).join('\n')}</div>}
    </div>
  )
}

export function ProducePanel({ openId, setOpenId }: { openId: number | null; setOpenId: (n: number | null) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const list = useQuery({
    queryKey: ['trend-prods'],
    queryFn: () => api.get<ListResp>('/api/v1/trends/productions'),
    refetchInterval: (q) => (Object.values(q.state.data?.jobs ?? {}).some((j) => j.running) ? 2500 : 15000),
  })
  const d = list.data

  async function wake() {
    try {
      const r = await api.post<{ state: string }>('/api/v1/trends/comfy/wake')
      toast.info(`ComfyUI: ${r.state}`)
      list.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function blank() {
    try {
      const r = await api.post<{ production: Prod }>('/api/v1/trends/productions', {
        title: 'New explainer',
        scenes: [{ id: 'intro', chapter: 'Intro', narration: '', visual: { type: 'title', heading: 'Title' } }],
      })
      qc.invalidateQueries({ queryKey: ['trend-prods'] })
      setOpenId(r.production.id)
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function nightNow() {
    try {
      await api.post('/api/v1/trends/auto/run')
      toast.info('Night run started (only does anything with auto mode on).')
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className="space-y-3">
        <div className="rounded-[var(--radius)] border border-border bg-surface p-3 text-[12px]">
          <div className="flex items-center gap-2">
            <span className={cn('h-2 w-2 rounded-full', d?.comfy_up ? 'bg-ok' : 'bg-ink-3')} />
            <span className="text-ink">ComfyUI {d?.comfy_up ? 'online' : 'offline'}</span>
            <Button size="sm" variant="ghost" className="ml-auto" onClick={wake}>
              <Power size={12} /> Wake
            </Button>
          </div>
          <div className="mt-1 text-ink-3">
            VibeVoice workflow {d?.voice_ready ? 'saved' : 'missing (Settings → Voice)'} · Render{' '}
            {d?.render.ok ? 'here' : 'on your PC'}
          </div>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={blank}>
              <Plus size={12} /> Blank
            </Button>
            <Button size="sm" variant="ghost" onClick={nightNow}>
              <Moon size={12} /> Night run now
            </Button>
          </div>
        </div>
        <JobLine name="voice" job={d?.jobs.voice} />
        <JobLine name="render" job={d?.jobs.render} />
        <JobLine name="auto" job={d?.jobs.auto} />
        {(d?.productions ?? []).map((p) => (
          <button
            key={p.id}
            onClick={() => setOpenId(p.id)}
            className={cn(
              'w-full rounded-[var(--radius)] border bg-surface p-3 text-left hover:border-border-strong',
              openId === p.id ? 'border-accent' : 'border-border',
            )}
          >
            <div className="truncate text-[13px] font-semibold text-ink">{p.title}</div>
            <div className="mt-1 flex flex-wrap gap-x-2 text-[11.5px] text-ink-3">
              <span>{p.scene_count} scenes</span>
              <span>~{mmss(p.seconds)}</span>
              <span className={p.voiced === p.scene_count ? 'text-ok' : ''}>{p.voiced} voiced</span>
              <span>{p.status}</span>
              {p.auto ? <span className="text-accent">auto</span> : null}
              <span>{ago(p.updated)}</span>
            </div>
          </button>
        ))}
        {d && d.productions.length === 0 && (
          <div className="text-[12px] text-ink-3">No productions yet. Open an idea and press "Make explainer".</div>
        )}
      </div>
      <div>
        {openId ? (
          <Editor key={openId} id={openId} jobs={d?.jobs ?? {}} renderOk={!!d?.render.ok} onDeleted={() => setOpenId(null)} />
        ) : (
          <EmptyState icon={Clapperboard} title="Pick a production" hint="Scenes, voice, render and the StudioForge package live here." />
        )}
      </div>
    </div>
  )
}

function Editor({ id, jobs, renderOk, onDeleted }: { id: number; jobs: Record<string, JobView>; renderOk: boolean; onDeleted: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({
    queryKey: ['trend-prod', id],
    queryFn: () => api.get<Detail>(`/api/v1/trends/productions/${id}`),
    refetchInterval: jobs.voice?.running || jobs.render?.running ? 3000 : false,
  })
  const [title, setTitle] = useState('')
  const [scenes, setScenes] = useState<Scene[]>([])
  const [dirty, setDirty] = useState(false)
  useEffect(() => {
    if (q.data && !dirty) {
      setTitle(q.data.production.title)
      setScenes(q.data.production.scenes ?? [])
    }
  }, [q.data, dirty])
  const p = q.data?.production
  if (!p) return <div className="text-[13px] text-ink-3">Loading…</div>

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['trend-prods'] })
    q.refetch()
  }
  const upd = (i: number, patch: Partial<Scene>) => {
    setScenes(scenes.map((s, j) => (j === i ? { ...s, ...patch } : s)))
    setDirty(true)
  }
  const updVis = (i: number, patch: Partial<Scene['visual']>) => upd(i, { visual: { ...scenes[i].visual, ...patch } })

  async function save() {
    try {
      await api.post(`/api/v1/trends/productions/${id}`, { title, scenes, idea_id: p!.idea_id })
      setDirty(false)
      toast.success('Saved. Audio kept for unchanged lines.')
      refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function act(path: string, body: unknown, msg: string) {
    try {
      await api.post(`/api/v1/trends/productions/${id}/${path}`, body)
      toast.info(msg)
      refresh()
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function del() {
    if (!confirm(`Delete "${p!.title}" and its audio?`)) return
    await api.del(`/api/v1/trends/productions/${id}`)
    qc.invalidateQueries({ queryKey: ['trend-prods'] })
    onDeleted()
  }
  async function uploadAsset(f?: File) {
    if (!f) return
    const fd = new FormData()
    fd.append('file', f)
    const r = await fetch(`/api/v1/trends/productions/${id}/assets`, { method: 'POST', body: fd })
    const b = await r.json().catch(() => ({}))
    if (r.ok) toast.success(`Uploaded ${b.name}: use it as visual image/clip`)
    else toast.error(b.detail ?? 'upload failed')
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <Input value={title} onChange={(e) => { setTitle(e.target.value); setDirty(true) }} className="max-w-md text-[14px] font-semibold" />
          <Button size="sm" variant={dirty ? 'primary' : 'default'} disabled={!dirty} onClick={save}>Save</Button>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button size="sm" onClick={() => act('voice', {}, 'Voicing on ComfyUI…')} disabled={jobs.voice?.running}>
              <Mic size={13} /> Voice (VibeVoice)
            </Button>
            <Button size="sm" onClick={() => act('render', {}, 'Rendering with Remotion…')} disabled={!renderOk || jobs.render?.running} title={renderOk ? '' : 'No Node on this server'}>
              <Film size={13} /> Render here
            </Button>
            <a className="inline-flex h-7 items-center gap-1 rounded-md border border-border-strong px-2.5 text-[12.5px] text-ink hover:bg-surface-hover" href={`/api/v1/trends/productions/${id}/download`}>
              <Download size={13} /> Download package
            </a>
            <Button size="sm" variant="ghost" onClick={() => act('package', {}, 'Package built (and copied to the export folder if set)')}>
              <Package size={13} /> To export folder
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 border-t border-border px-4 py-2.5 text-[12px] text-ink-2">
          <span>~{mmss(p.seconds)} · {p.voiced}/{p.scenes?.length} voiced · {p.status}</span>
          {p.reference?.url && (
            <span>
              Shape from <a className="text-accent hover:underline" href={p.reference.url} target="_blank" rel="noreferrer">{p.reference.title || 'reference'}</a>
              {p.reference.chapter_count ? ` · ${p.reference.chapter_count} chapters ~${p.reference.avg_chapter_seconds}s · ${p.reference.wpm ?? '?'} wpm` : ''}
            </span>
          )}
          <div className="ml-auto">
            <Switch checked={!!p.auto} onChange={(v) => act('auto', { on: v }, v ? 'Approved for the night run' : 'Removed from the night run')} label="Night run" hint="voice, render, package automatically" />
          </div>
        </div>
        {p.render?.state === 'failed' && <div className="border-t border-border px-4 py-2 text-[12px] text-danger">Render failed: {p.render.error}</div>}
        {q.data?.has_video && (
          <video controls className="w-full border-t border-border bg-black" src={`/api/v1/trends/productions/${id}/video?v=${p.updated}`} />
        )}
      </Card>

      {scenes.map((s, i) => (
        <Card key={s.id} className="p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <code className="text-[11px] text-ink-3">{s.id}</code>
            <Input value={s.chapter} placeholder="Chapter" onChange={(e) => upd(i, { chapter: e.target.value })} className="w-48" />
            <select value={s.visual.type} onChange={(e) => updVis(i, { type: e.target.value })} className="h-8 rounded-md border border-border-strong bg-surface px-2 text-[12.5px]">
              {['title', 'bullets', 'code', 'benchmark', 'comparison', 'image', 'quote', 'clip'].map((t) => <option key={t}>{t}</option>)}
            </select>
            <select value={s.speaker} onChange={(e) => upd(i, { speaker: Number(e.target.value) })} className="h-8 rounded-md border border-border-strong bg-surface px-2 text-[12.5px]">
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>Speaker {n}</option>)}
            </select>
            {s.audio?.file ? (
              <audio controls className="h-8" src={`/api/v1/trends/productions/${id}/audio/${s.audio.file}`} />
            ) : (
              <span className="text-[11px] text-ink-3">no audio</span>
            )}
            <div className="ml-auto flex gap-1">
              <Button size="icon-sm" variant="ghost" aria-label="Re-voice" title="Re-voice this scene" onClick={() => act('voice', { scene_ids: [s.id], redo: true }, `Re-voicing ${s.id}`)}>
                <RefreshCw size={12} />
              </Button>
              <Button size="icon-sm" variant="ghost" aria-label="Remove scene" onClick={() => { setScenes(scenes.filter((_, j) => j !== i)); setDirty(true) }}>
                <Trash2 size={12} />
              </Button>
            </div>
          </div>
          <Textarea rows={3} value={s.narration} placeholder="Narration (spoken)" onChange={(e) => upd(i, { narration: e.target.value })} />
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <Input value={s.visual.heading ?? ''} placeholder="On-screen heading" onChange={(e) => updVis(i, { heading: e.target.value })} />
            {s.visual.type === 'bullets' && (
              <Input value={(s.visual.lines ?? []).join(' | ')} placeholder="bullet | bullet | bullet" onChange={(e) => updVis(i, { lines: e.target.value.split('|').map((x) => x.trim()).filter(Boolean) })} />
            )}
            {(s.visual.type === 'benchmark' || s.visual.type === 'comparison') && (
              <Input
                value={(s.visual.bench_ids ?? []).join(', ')}
                placeholder="benchmark ids, e.g. 3, 7 (Benchmarks tab)"
                onChange={(e) => updVis(i, { bench_ids: e.target.value.split(',').map((x) => parseInt(x.trim(), 10)).filter((n) => !Number.isNaN(n)) })}
              />
            )}
            {(s.visual.type === 'image' || s.visual.type === 'clip') && (
              <Input value={(s.visual.type === 'image' ? s.visual.image : s.visual.clip) ?? ''} placeholder="file name (upload below)" onChange={(e) => updVis(i, s.visual.type === 'image' ? { image: e.target.value } : { clip: e.target.value })} />
            )}
            {s.visual.type === 'code' && (
              <Textarea rows={3} value={s.visual.code ?? ''} placeholder="code" onChange={(e) => updVis(i, { code: e.target.value })} className="font-mono text-[12px] md:col-span-2" />
            )}
            {s.visual.bench_ids && q.data?.bench && (
              <div className="text-[11.5px] text-ink-3 md:col-span-2">
                {s.visual.bench_ids.map((b) => q.data!.bench[String(b)] ? `${q.data!.bench[String(b)].display} ${q.data!.bench[String(b)].time || q.data!.bench[String(b)].status}` : `#${b} missing`).join(' · ')}
              </div>
            )}
          </div>
        </Card>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => { setScenes([...scenes, { id: `s${Date.now() % 100000}`, chapter: scenes.at(-1)?.chapter ?? '', speaker: 1, narration: '', visual: { type: 'bullets', lines: [] } }]); setDirty(true) }}>
          <Plus size={13} /> Scene
        </Button>
        <label className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-md border border-border-strong px-2.5 text-[12.5px] text-ink hover:bg-surface-hover">
          <Upload size={13} /> Image / clip
          <input type="file" accept="image/*,video/*" className="hidden" onChange={(e) => uploadAsset(e.target.files?.[0])} />
        </label>
        <Button size="sm" variant="danger" className="ml-auto" onClick={del}>
          <Trash2 size={13} /> Delete production
        </Button>
      </div>

      <Card>
        <CardHeader title="Scripts" subtitle="Copy to the PC, or download the whole package above." />
        <div className="grid gap-4 px-4 pb-4 md:grid-cols-2">
          <Section title="script.md (StudioForge)" action={<Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(p.script_md ?? '').then(() => toast.success('Copied'))}>Copy</Button>}>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-3 text-[11.5px] text-ink">{p.script_md}</pre>
          </Section>
          <Section title="VibeVoice script" action={<Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(q.data?.voice_script ?? '').then(() => toast.success('Copied'))}>Copy</Button>}>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-3 text-[11.5px] text-ink">{q.data?.voice_script}</pre>
          </Section>
        </div>
      </Card>
    </div>
  )
}
