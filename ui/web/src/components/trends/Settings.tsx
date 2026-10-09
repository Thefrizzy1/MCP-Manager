import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Textarea } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { Switch } from '@/components/ui/Switch'
import { useToast } from '@/components/ui/Toast'

type Conf = Record<string, unknown> & {
  weights: Record<string, number>
  benchmark: { enabled: boolean; auto_min_hot: number; timeout_min: number; templates: Record<string, unknown> }
}

const LISTS: [string, string, string][] = [
  ['youtube_channels', 'Competitor channels', '@handle, channel URL or UC… id, one per line'],
  ['subreddits', 'Subreddits', 'without r/'],
  ['hf_orgs', 'Hugging Face orgs', 'new repos here are early signals; unknown model names become new families'],
  ['hf_quant_authors', 'Quant uploaders', 'GGUF / fp8 uploads here mean "runs on 4 GB soon"'],
  ['github_repos', 'GitHub repos (PRs)', 'owner/name; "Add X pipeline" PRs land days before weights'],
  ['github_users', 'GitHub users (new repos)', 'a new wrapper repo is a release-day signal'],
  ['families', 'Model families', 'what the matcher recognises; longer names win'],
  ['closed_families', 'Closed / API-only', 'still tracked, lower niche fit'],
  ['hype_words', 'Excitement words', 'mark a thread as hyped'],
  ['lowvram_words', 'Low-VRAM words', 'mark a competitor video as a low-VRAM take'],
]

const lines = (v: unknown) => (Array.isArray(v) ? (v as string[]).join('\n') : '')
const unlines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)

export function SettingsPanel() {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({
    queryKey: ['trend-config'],
    queryFn: () => api.get<{ config: Conf; default_studio_prompt: string }>('/api/v1/trends/config'),
  })
  const [c, setC] = useState<Conf | null>(null)
  const [text, setText] = useState<Record<string, string>>({})
  const [tpl, setTpl] = useState('')
  useEffect(() => {
    if (q.data && !c) {
      setC(q.data.config)
      setText(Object.fromEntries(LISTS.map(([k]) => [k, lines(q.data.config[k])])))
      setTpl(JSON.stringify(q.data.config.benchmark.templates ?? {}, null, 2))
    }
  }, [q.data, c])
  if (!c) return <div className="text-[13px] text-ink-3">Loading…</div>

  const set = (k: string, v: unknown) => setC({ ...c, [k]: v })

  async function save() {
    let templates: unknown
    try {
      templates = JSON.parse(tpl || '{}')
    } catch {
      toast.error('Benchmark templates are not valid JSON')
      return
    }
    const body: Record<string, unknown> = { ...c }
    for (const [k] of LISTS) body[k] = unlines(text[k] ?? '')
    body.benchmark = { ...c!.benchmark, templates }
    try {
      const r = await api.post<{ config: Conf }>('/api/v1/trends/config', body)
      setC(r.config)
      toast.success('Saved. Schedules updated.')
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
      qc.invalidateQueries({ queryKey: ['trend-studio'] })
    } catch (e) {
      toast.error(String(e))
    }
  }

  const num = (k: string, min = 0, max = 100) => (
    <Input
      type="number"
      min={min}
      max={max}
      value={String(c[k] ?? '')}
      onChange={(e) => set(k, Number(e.target.value))}
      className="w-24"
    />
  )

  return (
    <div className="space-y-4 pb-10">
      <div className="sticky top-0 z-10 -mx-1 flex justify-end bg-bg/90 px-1 py-2 backdrop-blur">
        <Button variant="primary" onClick={save}>
          Save settings
        </Button>
      </div>

      <Card>
        <CardHeader title="Schedules" subtitle="Both appear as normal schedules on the Agents page." />
        <div className="grid gap-4 px-4 pb-4 md:grid-cols-2">
          <div className="space-y-2">
            <Switch checked={!!c.schedule_enabled} onChange={(v) => set('schedule_enabled', v)} label="Collect automatically" hint="Off means the radar only updates when you press Collect." />
            <Field label="Collect cron" hint="Default every 3 hours.">
              <Input value={String(c.schedule_cron)} onChange={(e) => set('schedule_cron', e.target.value)} />
            </Field>
            <Field label="Your channel" hint="Your uploads mark models as done and power 'looks finished'.">
              <Input value={String(c.my_channel ?? '')} onChange={(e) => set('my_channel', e.target.value)} />
            </Field>
          </div>
          <div className="space-y-2">
            <Switch checked={!!c.studio_enabled} onChange={(v) => set('studio_enabled', v)} label="Run the Idea Studio daily" hint="Off means it costs nothing until you press Run now." />
            <Field label="Studio cron" hint="Default 06:30, after a collect.">
              <Input value={String(c.studio_cron)} onChange={(e) => set('studio_cron', e.target.value)} />
            </Field>
            <div className="flex gap-3">
              <Field label="Max models per run">{num('studio_max_candidates', 1, 15)}</Field>
              <Field label="Model (optional)" hint="empty = your agent default">
                <Input value={String(c.studio_model ?? '')} onChange={(e) => set('studio_model', e.target.value)} className="w-44" />
              </Field>
            </div>
          </div>
          <Field label="Studio task prompt" hint="Empty uses the built-in prompt. {max_candidates} is filled in." className="md:col-span-2">
            <Textarea
              rows={8}
              value={String(c.studio_prompt ?? '')}
              placeholder={q.data?.default_studio_prompt}
              onChange={(e) => set('studio_prompt', e.target.value)}
              className="font-mono text-[12px]"
            />
          </Field>
        </div>
      </Card>

      <Card>
        <CardHeader title="Scoring" subtitle="Weights are relative. Saturation is subtracted. Changes rescore every model at once." />
        <div className="flex flex-wrap gap-4 px-4 pb-4">
          {Object.entries(c.weights).map(([k, v]) => (
            <Field key={k} label={k}>
              <Input
                type="number"
                min={0}
                value={String(v)}
                onChange={(e) => set('weights', { ...c.weights, [k]: Number(e.target.value) })}
                className="w-20"
              />
            </Field>
          ))}
          <Field label="Alert at hot ≥">{num('alert_threshold')}</Field>
          <div className="self-end pb-1">
            <Switch checked={!!c.alert_ntfy} onChange={(v) => set('alert_ntfy', v)} label="Push alerts via ntfy" hint="Once per model, uses your ntfy connection." />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Sources" subtitle="One entry per line. Keys come from your existing connections (YouTube, GitHub, Hugging Face, Reddit)." />
        <div className="grid gap-4 px-4 pb-4 md:grid-cols-2">
          {LISTS.map(([k, label, hint]) => (
            <Field key={k} label={label} hint={hint}>
              <Textarea rows={5} value={text[k] ?? ''} onChange={(e) => setText({ ...text, [k]: e.target.value })} className="text-[12.5px]" />
            </Field>
          ))}
          <div className="space-y-2">
            <Switch checked={!!c.arxiv_enabled} onChange={(v) => set('arxiv_enabled', v)} label="arXiv" hint="Weeks of lead, many false starts." />
            <Switch checked={!!c.hf_papers_enabled} onChange={(v) => set('hf_papers_enabled', v)} label="HF daily papers" hint="The papers the community upvotes." />
            <Switch checked={!!c.hn_enabled} onChange={(v) => set('hn_enabled', v)} label="Hacker News" hint="Broader tech buzz." />
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Auto-benchmark (ComfyUI)"
          subtitle="Per family, an API-format workflow (ComfyUI → Save (API)). Write {{seed}} where the seed goes. Runs on COMFYUI_URL; results are real timings, OOM counts as a result."
        />
        <div className="space-y-3 px-4 pb-4">
          <div className="flex flex-wrap items-end gap-4">
            <Switch
              checked={c.benchmark.enabled}
              onChange={(v) => setC({ ...c, benchmark: { ...c.benchmark, enabled: v } })}
              label="Run automatically"
              hint="One model per collect, when weights are out and hot is high enough."
            />
            <Field label="Min hot">
              <Input type="number" value={String(c.benchmark.auto_min_hot)} onChange={(e) => setC({ ...c, benchmark: { ...c.benchmark, auto_min_hot: Number(e.target.value) } })} className="w-20" />
            </Field>
            <Field label="Timeout (min)">
              <Input type="number" value={String(c.benchmark.timeout_min)} onChange={(e) => setC({ ...c, benchmark: { ...c.benchmark, timeout_min: Number(e.target.value) } })} className="w-20" />
            </Field>
          </div>
          <Field label="Templates" hint={'{"wan": {"workflow": {…API JSON…}, "note": "Q4 GGUF 480p 33 frames"}}'}>
            <Textarea rows={10} value={tpl} onChange={(e) => setTpl(e.target.value)} className="font-mono text-[11.5px]" />
          </Field>
        </div>
      </Card>
    </div>
  )
}
