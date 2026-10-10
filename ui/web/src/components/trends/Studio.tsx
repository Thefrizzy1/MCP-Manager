import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Play, Trash2, Upload, FileText, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'
import { Section } from './bits'
import type { SchedInfo } from './types'

interface StudioResp {
  brief: string
  prompt: string
  skills: { name: string; description: string; files: number; bytes: number }[]
  knowledge: { name: string; bytes: number }[]
  folder: string
  runs: { id: string; label: string; started: string; finished: string | null; ok: boolean; error?: string; cost_usd?: number }[]
  schedule: SchedInfo | null
}

async function upload(path: string, file: File, extra?: Record<string, string>) {
  const fd = new FormData()
  fd.append('file', file)
  for (const [k, v] of Object.entries(extra ?? {})) fd.append(k, v)
  const res = await fetch(path, { method: 'POST', body: fd })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  return body
}

function kb(n: number) {
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
}

export function StudioPanel() {
  const toast = useToast()
  const q = useQuery({
    queryKey: ['trend-studio'],
    queryFn: () => api.get<StudioResp>('/api/v1/trends/studio'),
    refetchInterval: 8000,
  })
  const [brief, setBrief] = useState('')
  const [dirty, setDirty] = useState(false)
  useEffect(() => {
    if (q.data && !dirty) setBrief(q.data.brief)
  }, [q.data, dirty])
  const d = q.data

  async function run() {
    try {
      await api.post('/api/v1/trends/studio/run')
      toast.success('Idea Studio queued. It shows up in Agents → runs, and ideas land on the Ideas tab.')
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function saveBrief() {
    try {
      await api.post('/api/v1/trends/studio/brief', { text: brief })
      setDirty(false)
      toast.success('CLAUDE.md saved')
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function onSkill(f?: File) {
    if (!f) return
    try {
      await upload('/api/v1/trends/studio/skills', f)
      toast.success(`Skill installed from ${f.name}`)
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function onKnowledge(files?: FileList | null) {
    if (!files) return
    for (const f of Array.from(files)) {
      try {
        await upload('/api/v1/trends/studio/knowledge', f)
      } catch (e) {
        toast.error(`${f.name}: ${String(e)}`)
      }
    }
    q.refetch()
  }

  async function del(kind: 'skills' | 'knowledge', name: string) {
    try {
      await api.del(`/api/v1/trends/studio/${kind}/${encodeURIComponent(name)}`)
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader
          title="Idea Studio"
          subtitle="Your Claude, run daily in a folder with your skills and knowledge. It turns hot models into ideas: titles, thumbnail mock, description, script."
          action={
            <Button variant="primary" size="sm" onClick={run}>
              <Play size={13} /> Run now
            </Button>
          }
        />
        <div className="px-4 pb-4 text-[12.5px] text-ink-2">
          <div>
            Schedule:{' '}
            {d?.schedule ? (
              <b className="text-ink">
                {d.schedule.enabled ? d.schedule.cron : 'off'}
                {d.schedule.enabled && d.schedule.next ? ` · next ${new Date(d.schedule.next).toLocaleString()}` : ''}
              </b>
            ) : (
              'not created yet'
            )}{' '}
            <span className="text-ink-3">(Settings tab)</span>
          </div>
          <div className="mt-1 text-ink-3">
            Folder on the server: <code className="text-[11.5px]">{d?.folder}</code>
          </div>
          <Section title="Recent runs">
            {d?.runs.length ? (
              <ul className="space-y-1">
                {d.runs.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-2">
                    <span className={r.ok ? 'text-ok' : r.finished ? 'text-danger' : 'text-warn'}>
                      {r.ok ? 'ok' : r.finished ? 'failed' : 'running'}
                    </span>
                    <span className="flex-1 truncate text-ink-2">{new Date(r.started).toLocaleString()}</span>
                    {r.error && <span className="truncate text-[11px] text-danger">{r.error}</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-ink-3">No runs yet.</div>
            )}
            <Button size="sm" variant="ghost" className="mt-2" onClick={() => navigate('agents')}>
              Open transcripts on Agents
            </Button>
          </Section>
        </div>
      </Card>

      <Card>
        <CardHeader title="Skills" subtitle="Upload a SKILL.md, a .zip or a .skill export (e.g. frizzy-youtube, thumbnail). Claude Code loads them from .claude/skills." />
        <div className="px-4 pb-4">
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-[12.5px] text-ink hover:bg-surface-hover">
            <Upload size={13} /> Upload skill
            <input type="file" accept=".md,.zip,.skill" className="hidden" onChange={(e) => onSkill(e.target.files?.[0])} />
          </label>
          <ul className="mt-3 space-y-2">
            {(d?.skills ?? []).map((s) => (
              <li key={s.name} className="flex items-start gap-2 rounded-md border border-border px-3 py-2">
                <Sparkles size={14} className="mt-0.5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-ink">{s.name}</div>
                  <div className="line-clamp-2 text-[11.5px] text-ink-3">{s.description || 'no description'}</div>
                  <div className="text-[11px] text-ink-3">
                    {s.files} files · {kb(s.bytes)}
                  </div>
                </div>
                <Button size="icon-sm" variant="ghost" aria-label={`Remove ${s.name}`} onClick={() => del('skills', s.name)}>
                  <Trash2 size={13} />
                </Button>
              </li>
            ))}
            {d && d.skills.length === 0 && <li className="text-[12px] text-ink-3">No skills yet.</li>}
          </ul>

          <Section title="Knowledge files">
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-[12.5px] text-ink hover:bg-surface-hover">
              <Upload size={13} /> Add .md / .txt / .csv / .json
              <input type="file" multiple accept=".md,.txt,.csv,.json" className="hidden" onChange={(e) => onKnowledge(e.target.files)} />
            </label>
            <ul className="mt-2 space-y-1">
              {(d?.knowledge ?? []).map((k) => (
                <li key={k.name} className="flex items-center gap-2 text-[12.5px]">
                  <FileText size={13} className="text-ink-3" />
                  <span className="flex-1 truncate text-ink">{k.name}</span>
                  <span className="text-[11px] text-ink-3">{kb(k.bytes)}</span>
                  <Button size="icon-sm" variant="ghost" aria-label={`Remove ${k.name}`} onClick={() => del('knowledge', k.name)}>
                    <Trash2 size={12} />
                  </Button>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader
          title="CLAUDE.md (channel brief)"
          subtitle="Read at the start of every studio run: who the channel is for, the rules, where things live."
          action={
            <Button size="sm" variant={dirty ? 'primary' : 'default'} disabled={!dirty} onClick={saveBrief}>
              Save
            </Button>
          }
        />
        <div className="px-4 pb-4">
          <Textarea
            rows={14}
            value={brief}
            onChange={(e) => {
              setBrief(e.target.value)
              setDirty(true)
            }}
            className="font-mono text-[12px]"
          />
          <Section title="Task prompt the run gets (edit in Settings)">
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-3 text-[11.5px] text-ink-2">
              {d?.prompt}
            </pre>
          </Section>
        </div>
      </Card>
    </div>
  )
}
