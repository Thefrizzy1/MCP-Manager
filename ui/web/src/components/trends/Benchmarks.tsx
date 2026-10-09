import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRightLeft, Download, Plus, Star, Trash2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select, Textarea } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'

interface Bench {
  id: number
  slug: string
  display: string
  status: string
  seconds: number | null
  time: string
  vram_gb: number | null
  hw: string
  grp: string
  mark: number
  source: string
  started: number
  notes: string
}
interface ListResp {
  benchmarks: Bench[]
  models: { slug: string; name: string }[]
}
interface ExportResp {
  studioforge: unknown
  notes_table: string
  comparison: { pct: number | null; saved_seconds: number | null; faster: boolean | null; label: string } | null
}

const EMPTY = { slug: '', label: '', time: '', status: 'ok', vram_gb: '', hw: '', group: '', mark: false }

export function BenchmarksPanel() {
  const toast = useToast()
  const q = useQuery({ queryKey: ['trend-bench'], queryFn: () => api.get<ListResp>('/api/v1/trends/benchmarks') })
  const [model, setModel] = useState('')
  const [sel, setSel] = useState<number[]>([])
  const [pair, setPair] = useState<[number | null, number | null]>([null, null])
  const [form, setForm] = useState({ ...EMPTY })
  const [table, setTable] = useState('')
  const [exp, setExp] = useState<ExportResp | null>(null)

  const rows = useMemo(() => (q.data?.benchmarks ?? []).filter((b) => !model || b.slug === model), [q.data, model])
  const groups = useMemo(() => {
    const m = new Map<string, Bench[]>()
    for (const b of rows) m.set(b.slug || '(no model)', [...(m.get(b.slug || '(no model)') ?? []), b])
    return [...m.entries()]
  }, [rows])
  const byId = useMemo(() => Object.fromEntries((q.data?.benchmarks ?? []).map((b) => [b.id, b])), [q.data])

  async function add() {
    try {
      await api.post('/api/v1/trends/benchmarks', { ...form, vram_gb: form.vram_gb === '' ? null : Number(form.vram_gb) })
      setForm({ ...EMPTY, slug: form.slug, hw: form.hw })
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function importTable() {
    try {
      const r = await api.post<{ imported: number }>('/api/v1/trends/benchmarks/import', { text: table, slug: form.slug, hw: form.hw })
      toast.success(`Imported ${r.imported} runs`)
      setTable('')
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function del(id: number) {
    await api.del(`/api/v1/trends/benchmarks/${id}`)
    setSel(sel.filter((x) => x !== id))
    q.refetch()
  }
  async function toggleMark(b: Bench) {
    await api.post(`/api/v1/trends/benchmarks/${b.id}`, { mark: b.mark ? 0 : 1 })
    q.refetch()
  }
  async function exportSel() {
    try {
      const ids = sel.length ? sel : rows.map((r) => r.id)
      const heading = model ? `${q.data?.models.find((m) => m.slug === model)?.name ?? model} render time` : 'Render time'
      setExp(await api.post<ExportResp>('/api/v1/trends/benchmarks/export', { ids, pair: pair.every((x) => x) ? pair : [], heading }))
    } catch (e) {
      toast.error(String(e))
    }
  }
  function downloadJson() {
    if (!exp) return
    const blob = new Blob([JSON.stringify(exp.studioforge, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'studioforge_import.json'
    a.click()
  }

  const A = pair[0] ? byId[pair[0]] : null
  const B = pair[1] ? byId[pair[1]] : null
  const delta = A?.seconds && B?.seconds ? { saved: A.seconds - B.seconds, pct: Math.round(((A.seconds - B.seconds) / A.seconds) * 100) } : null
  const fmt = (s: number) => {
    const v = Math.round(Math.abs(s)), h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), x = v % 60
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={model} onChange={(e) => setModel(e.target.value)} className="w-56">
          <option value="">All models</option>
          {[...new Set((q.data?.benchmarks ?? []).map((b) => b.slug).filter(Boolean))].map((s) => (
            <option key={s} value={s}>{q.data?.models.find((m) => m.slug === s)?.name ?? s}</option>
          ))}
        </Select>
        <span className="text-[12px] text-ink-3">Tick runs to export; pick A and B to compare. Ids go into explainer scenes.</span>
        <Button size="sm" variant="primary" className="ml-auto" onClick={exportSel}>
          <Download size={13} /> StudioForge export
        </Button>
      </div>

      {(A || B) && (
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-6">
            <div><div className="text-[11px] uppercase tracking-wide text-ink-3">A · baseline</div><div className="text-[15px] font-semibold text-ink">{A?.display ?? 'pick'}</div><div className="text-[22px] font-bold tabular-nums text-ink">{A?.time || A?.status || '–'}</div></div>
            <ArrowRightLeft size={18} className="text-ink-3" />
            <div><div className="text-[11px] uppercase tracking-wide text-ink-3">B · variant</div><div className="text-[15px] font-semibold text-ink">{B?.display ?? 'pick'}</div><div className="text-[22px] font-bold tabular-nums text-ink">{B?.time || B?.status || '–'}</div></div>
            {delta && (
              <div className={cn('ml-auto text-[26px] font-black', delta.saved > 0 ? 'text-ok' : 'text-danger')}>
                {Math.abs(delta.pct)}% {delta.saved > 0 ? 'faster' : 'slower'} <span className="text-[14px] font-semibold">({fmt(delta.saved)} {delta.saved > 0 ? 'saved' : 'added'})</span>
              </div>
            )}
          </div>
          {A && B && A.hw !== B.hw && <div className="mt-2 text-[11.5px] text-warn">Different hardware ({A.hw || '?'} vs {B.hw || '?'}): not an A/B on the same rig.</div>}
        </Card>
      )}

      {groups.length === 0 ? (
        <EmptyState icon={Star} title="No runs yet" hint="Add a run below, import a StudioForge notes table, or let the auto-benchmark fill this from ComfyUI." />
      ) : (
        groups.map(([slug, items]) => {
          const max = Math.max(...items.map((b) => b.seconds ?? 0), 1)
          return (
            <Card key={slug}>
              <CardHeader title={q.data?.models.find((m) => m.slug === slug)?.name ?? slug} subtitle={`${items.length} runs`} />
              <div className="space-y-2 px-4 pb-4">
                {items.map((b) => {
                  const ok = b.status === 'ok' && b.seconds
                  return (
                    <div key={b.id} className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2">
                      <input type="checkbox" checked={sel.includes(b.id)} onChange={(e) => setSel(e.target.checked ? [...sel, b.id] : sel.filter((x) => x !== b.id))} />
                      <div className="min-w-0">
                        <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
                          <span className="truncate text-ink">
                            <code className="mr-1.5 text-[10.5px] text-ink-3">#{b.id}</code>{b.display}
                            <span className="ml-1.5 text-[11px] text-ink-3">{[b.hw, b.vram_gb ? `${b.vram_gb} GB peak` : '', b.source].filter(Boolean).join(' · ')}</span>
                          </span>
                          <span className={cn('shrink-0 font-semibold tabular-nums', !ok ? 'text-danger' : b.mark ? 'text-warn' : 'text-ink')}>{ok ? b.time : b.status.toUpperCase()}</span>
                        </div>
                        <div className="mt-1 h-2 overflow-hidden rounded-full bg-surface-hover">
                          <div className={cn('h-full rounded-full', !ok ? 'bg-danger' : b.mark ? 'bg-warn' : 'bg-ink-3')} style={{ width: ok ? `${((b.seconds ?? 0) / max) * 100}%` : '100%' }} />
                        </div>
                      </div>
                      <div className="flex items-center gap-0.5">
                        <Button size="sm" variant={pair[0] === b.id ? 'primary' : 'ghost'} onClick={() => setPair([b.id, pair[1]])}>A</Button>
                        <Button size="sm" variant={pair[1] === b.id ? 'primary' : 'ghost'} onClick={() => setPair([pair[0], b.id])}>B</Button>
                        <Button size="icon-sm" variant="ghost" aria-label="Highlight" onClick={() => toggleMark(b)}><Star size={12} className={b.mark ? 'fill-current text-warn' : ''} /></Button>
                        <Button size="icon-sm" variant="ghost" aria-label="Delete" onClick={() => del(b.id)}><Trash2 size={12} /></Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </Card>
          )
        })
      )}

      {exp && (
        <Card>
          <CardHeader
            title="StudioForge export"
            subtitle="Graphics → Import in StudioForge takes the JSON; the table is benchmarks.py's notes format."
            action={<Button size="sm" onClick={downloadJson}><Download size={13} /> studioforge_import.json</Button>}
          />
          <div className="grid gap-3 px-4 pb-4 md:grid-cols-2">
            <pre className="max-h-64 overflow-auto rounded-md border border-border bg-surface-2 p-3 text-[11px]">{JSON.stringify(exp.studioforge, null, 2)}</pre>
            <pre className="max-h-64 overflow-auto rounded-md border border-border bg-surface-2 p-3 text-[11px]">{exp.notes_table}</pre>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader title="Add a run you measured" subtitle="Real measurements only: these numbers end up on screen." />
        <div className="flex flex-wrap items-end gap-2 px-4 pb-3">
          <Select value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} className="w-44">
            <option value="">model…</option>
            {(q.data?.models ?? []).map((m) => <option key={m.slug} value={m.slug}>{m.name}</option>)}
          </Select>
          <Input placeholder="label (e.g. Q4 GGUF · Sage)" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} className="w-56" />
          <Input placeholder="time 54:48" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} className="w-28" />
          <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="w-24">
            {['ok', 'oom', 'dnf', 'error'].map((s) => <option key={s}>{s}</option>)}
          </Select>
          <Input placeholder="VRAM GB" value={form.vram_gb} onChange={(e) => setForm({ ...form, vram_gb: e.target.value })} className="w-24" />
          <Input placeholder="hardware (RTX 3050 Ti 4GB)" value={form.hw} onChange={(e) => setForm({ ...form, hw: e.target.value })} className="w-52" />
          <Button size="md" onClick={add}><Plus size={13} /> Add</Button>
        </div>
        <div className="px-4 pb-4">
          <Textarea rows={4} placeholder={'| group | label | file | time | mark |\n|---|---|---|---|---|\n| gguf | Q3_K_M · 5 s | 0002 | 50:19 |  |'} value={table} onChange={(e) => setTable(e.target.value)} className="font-mono text-[11.5px]" />
          <Button size="sm" className="mt-2" onClick={importTable} disabled={!table.trim()}><Upload size={13} /> Import notes table (uses the model + hardware above)</Button>
        </div>
      </Card>
    </div>
  )
}
