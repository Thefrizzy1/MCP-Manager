import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Pin, EyeOff, CheckCircle2, Gauge, GitMerge } from 'lucide-react'
import { api } from '@/lib/api'
import { Drawer } from '@/components/ui/Drawer'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'
import { ActionTag, BacktestChart, HotBar, Meter, Section, StageTrack } from './bits'
import { ago, date, hours, type CandidateDetail } from './types'

const KIND_TONE: Record<string, string> = {
  code: 'text-accent',
  weights: 'text-ok',
  gguf: 'text-ok',
  repo: 'text-ink',
  paper: 'text-ink-2',
  buzz: 'text-warn',
  video: 'text-danger',
  short: 'text-danger',
  own: 'text-accent',
}

function metricLine(m: Record<string, unknown>): string {
  const bits: string[] = []
  for (const k of ['score', 'comments', 'views', 'likes', 'downloads', 'upvotes', 'stars']) {
    const v = m[k]
    if (typeof v === 'number') bits.push(`${k} ${v.toLocaleString()}`)
  }
  if (m.merged) bits.push('merged')
  return bits.join(' · ')
}

export function CandidateDrawer({
  slug,
  onClose,
  onOpenIdea,
  threshold,
}: {
  slug: string | null
  onClose: () => void
  onOpenIdea: (id: number) => void
  threshold: number
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [mergeInto, setMergeInto] = useState('')
  const [tpl, setTpl] = useState('')
  const q = useQuery({
    queryKey: ['trend-cand', slug],
    queryFn: () => api.get<CandidateDetail>(`/api/v1/trends/candidates/${encodeURIComponent(slug ?? '')}`),
    enabled: !!slug,
    refetchInterval: 10000,
  })
  const d = q.data
  const c = d?.candidate
  const p = c?.score ?? {}

  async function setStatus(status: string) {
    if (!slug) return
    try {
      await api.post(`/api/v1/trends/candidates/${encodeURIComponent(slug)}/status`, { status })
      toast.success(`Marked ${status}`)
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function merge() {
    if (!slug || !mergeInto.trim()) return
    try {
      const r = await api.post<{ moved: number }>(`/api/v1/trends/candidates/${encodeURIComponent(slug)}/merge`, {
        into: mergeInto.trim(),
      })
      toast.success(`Merged ${r.moved} signals into ${mergeInto}`)
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
      onClose()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function bench() {
    if (!slug) return
    try {
      await api.post('/api/v1/trends/benchmark', { slug, template: tpl })
      toast.info('Benchmark queued on ComfyUI. Results land here when it finishes.')
    } catch (e) {
      toast.error(String(e))
    }
  }

  const bt = d?.backtest

  return (
    <Drawer open={!!slug} onClose={onClose} title={c ? c.name : 'Loading…'}>
      {!c ? (
        <div className="text-[13px] text-ink-3">{q.error ? String(q.error) : 'Loading…'}</div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <HotBar hot={c.hot} threshold={threshold} />
            <StageTrack stage={c.stage} />
            <ActionTag action={c.action} />
          </div>
          <div className="mb-5 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setStatus(c.status === 'pinned' ? 'watch' : 'pinned')}>
              <Pin size={13} /> {c.status === 'pinned' ? 'Unpin' : 'Pin'}
            </Button>
            <Button size="sm" onClick={() => setStatus('covered')}>
              <CheckCircle2 size={13} /> I covered it
            </Button>
            <Button size="sm" variant="danger" onClick={() => setStatus('dismissed')}>
              <EyeOff size={13} /> Dismiss
            </Button>
          </div>

          <Section title="Why this score">
            <div className="grid grid-cols-2 gap-x-5 gap-y-3">
              <Meter label="Stage" value={p.stage} hint="code/weights are the sweet spot" />
              <Meter label="Momentum" value={p.momentum} hint="signals in the last 48h" />
              <Meter
                label="Buzz"
                value={p.buzz}
                hint={`${p.threads_7d ?? 0} threads · ${Math.round((p.excitement ?? 0) * 100)}% excited`}
              />
              <Meter label="Demand" value={p.demand} hint={`best ${(p.best_views_per_day ?? 0).toLocaleString()} views/day`} />
              <Meter label="Niche fit" value={p.fit} hint="video, ≤14B, GGUF, ComfyUI" />
              <Meter
                label="Saturation"
                value={p.saturation}
                negative
                hint={`${p.creator_videos_10d ?? 0} creator videos, ${p.lowvram_videos_10d ?? 0} low-VRAM (10d)`}
              />
            </div>
            <div className="mt-3 text-[11.5px] text-ink-3">
              Confirmed by {p.sources_confirming ?? 0} source types (+{p.confirm_bonus ?? 0}) · idea caught{' '}
              {p.idea_catches ?? 0}× (+{p.catch_bonus ?? 0})
            </div>
          </Section>

          {bt?.ok && (
            <Section title="Replay (backtest)">
              <BacktestChart bt={bt} />
              <div className="mt-2 grid grid-cols-2 gap-1 text-[12px] text-ink-2">
                <span>Alert → weights: <b className="text-ink">{hours(bt.lead_alert_to_release_h)}</b></span>
                <span>Alert → 1st video: <b className="text-ink">{hours(bt.lead_alert_to_first_video_h)}</b></span>
                <span>Your upload: <b className="text-ink">{date(bt.own_upload)}</b></span>
                <span>Your rank: <b className="text-ink">{bt.own_rank ?? '–'}</b></span>
              </div>
            </Section>
          )}

          {d.ideas.length > 0 && (
            <Section title={`Ideas (${d.ideas.length})`}>
              <div className="space-y-1.5">
                {d.ideas.map((i) => (
                  <button
                    key={i.id}
                    onClick={() => onOpenIdea(i.id)}
                    className="flex w-full items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-left hover:bg-surface-hover"
                  >
                    <span className="min-w-0 truncate text-[13px] text-ink">{i.title}</span>
                    <span className="shrink-0 text-[11px] text-ink-3">
                      {i.catches > 1 ? `${i.catches}× · ` : ''}
                      {i.status}
                    </span>
                  </button>
                ))}
              </div>
            </Section>
          )}

          <Section title="Benchmark on ComfyUI">
            <div className="flex items-center gap-2">
              <Select value={tpl} onChange={(e) => setTpl(e.target.value)} className="w-44">
                <option value="">{c.family} (family default)</option>
                {d.templates.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
              <Button size="sm" onClick={bench}>
                <Gauge size={13} /> Run
              </Button>
            </div>
            {d.templates.length === 0 && (
              <div className="mt-1.5 text-[11.5px] text-ink-3">
                No workflow templates yet. Paste an API-format workflow per family in Settings.
              </div>
            )}
            {d.benchmarks.length > 0 && (
              <table className="mt-3 w-full text-[12px]">
                <tbody>
                  {d.benchmarks.map((b) => (
                    <tr key={b.id} className="border-t border-border">
                      <td className="py-1.5 text-ink-3">{date(b.started)}</td>
                      <td className="py-1.5 font-medium text-ink">{b.status}</td>
                      <td className="py-1.5 tabular-nums">{b.seconds != null ? `${(b.seconds / 60).toFixed(1)} min` : '–'}</td>
                      <td className="py-1.5 tabular-nums">
                        {b.vram_peak ? `${(b.vram_peak / 1024 ** 3).toFixed(1)} GB` : '–'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <Section title={`Timeline (${d.signals.length} signals)`}>
            <ol className="space-y-2">
              {[...d.signals].reverse().map((s) => (
                <li key={s.id} className="text-[12.5px]">
                  <div className="flex items-baseline gap-2">
                    <span className={`w-14 shrink-0 font-semibold uppercase tracking-wide ${KIND_TONE[s.kind] ?? 'text-ink-2'} text-[10.5px]`}>
                      {s.kind}
                    </span>
                    <span className="min-w-0 flex-1 text-ink">
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer" className="hover:underline">
                          {s.title || s.ext_id} <ExternalLink size={11} className="inline" />
                        </a>
                      ) : (
                        s.title
                      )}
                    </span>
                  </div>
                  <div className="ml-16 text-[11px] text-ink-3">
                    {s.source} · {date(s.ts)} ({ago(s.ts)}){metricLine(s.metrics) ? ` · ${metricLine(s.metrics)}` : ''}
                  </div>
                </li>
              ))}
            </ol>
          </Section>

          <Section title="Merge a duplicate">
            <div className="flex items-center gap-2">
              <Input placeholder="target slug, e.g. ltx-2" value={mergeInto} onChange={(e) => setMergeInto(e.target.value)} />
              <Button size="sm" onClick={merge}>
                <GitMerge size={13} /> Merge into
              </Button>
            </div>
            <div className="mt-1 text-[11.5px] text-ink-3">Moves this model's signals into another and removes this row.</div>
          </Section>
        </>
      )}
    </Drawer>
  )
}
