import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Input'
import { Stat } from '@/components/ui/Stat'
import { useToast } from '@/components/ui/Toast'
import { date, hours, type Job } from './types'

interface ResearchRow {
  id: string
  title: string
  channel: string
  views?: number | null
  vpd?: number
  subs?: number | null
  views_per_sub?: number
  published?: string
  thumb: string
}
interface ResearchResp {
  query: string
  exact: boolean
  rows: ResearchRow[]
  patterns: string[]
}
interface BtRow {
  slug: string
  name: string
  alert: number | null
  release: number | null
  first_competitor_video: number | null
  own_upload: number | null
  own_rank: number | null
  lead_alert_to_release_h: number | null
  lead_alert_to_first_video_h: number | null
  caught_before_release: boolean
}
interface BtResp {
  rows: BtRow[]
  summary: {
    candidates_with_release: number
    covered_by_you: number
    recall_on_your_videos: number | null
    precision_alert_became_release: number | null
    median_lead_alert_to_release_h: number | null
  }
}

function pct(v: number | null | undefined) {
  return v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`
}

export function ResearchPanel() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [period, setPeriod] = useState('month')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<ResearchResp | null>(null)

  async function run() {
    if (!query.trim()) return
    setBusy(true)
    try {
      setRes(await api.post<ResearchResp>('/api/v1/trends/research', { query: query.trim(), period }))
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const rows = [...(res?.rows ?? [])].sort((a, b) => (b.vpd ?? 0) - (a.vpd ?? 0))
  return (
    <Card>
      <CardHeader title="Title & thumbnail research" subtitle="What other creators shipped on a topic, ranked by views/day. The Idea Studio calls the same tool." />
      <div className="px-4 pb-4">
        <div className="flex flex-wrap gap-2">
          <Input
            placeholder="e.g. Wan 2.5 ComfyUI"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run()}
            className="max-w-sm"
          />
          <Select value={period} onChange={(e) => setPeriod(e.target.value)} className="w-32">
            {['week', 'month', 'year', 'relevance'].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </Select>
          <Button variant="primary" size="md" onClick={run} disabled={busy}>
            <Search size={13} /> {busy ? 'Searching…' : 'Search'}
          </Button>
        </div>
        {res && (
          <>
            {!res.exact && (
              <div className="mt-3 text-[12px] text-warn">No YouTube API key: views are rounded and there is no views/day.</div>
            )}
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {rows.map((r) => (
                <a key={r.id} href={`https://youtu.be/${r.id}`} target="_blank" rel="noreferrer" className="group">
                  <img src={r.thumb} alt="" loading="lazy" className="aspect-video w-full rounded-md border border-border object-cover" />
                  <div className="mt-1 line-clamp-2 text-[12.5px] font-medium text-ink group-hover:underline">{r.title}</div>
                  <div className="text-[11px] text-ink-3">
                    {r.channel}
                    {r.vpd ? ` · ${Math.round(r.vpd).toLocaleString()}/day` : r.views ? ` · ${r.views.toLocaleString()} views` : ''}
                    {r.views_per_sub ? ` · ${r.views_per_sub}× subs` : ''}
                  </div>
                </a>
              ))}
            </div>
            <div className="mt-4 rounded-md border border-border bg-surface-2 p-3 text-[12px] text-ink-2">
              <div className="mb-1 font-semibold text-ink">Title patterns in this set</div>
              {res.patterns.map((p) => (
                <div key={p}>{p}</div>
              ))}
            </div>
          </>
        )}
      </div>
    </Card>
  )
}

export function BacktestPanel({ job }: { job?: Job }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const q = useQuery({
    queryKey: ['trend-backtest'],
    queryFn: () => api.get<BtResp>('/api/v1/trends/backtest'),
    refetchInterval: job?.running ? 5000 : false,
  })
  const s = q.data?.summary

  async function backfill() {
    if (!name.trim()) return
    try {
      await api.post('/api/v1/trends/backfill', { name: name.trim() })
      toast.info(`Importing history for ${name}. GitHub search is rate-limited, give it a minute.`)
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Released models" value={s?.candidates_with_release ?? '–'} />
        <Stat label="Recall on your videos" value={pct(s?.recall_on_your_videos)} hint={`${s?.covered_by_you ?? 0} you covered`} />
        <Stat label="Precision" value={pct(s?.precision_alert_became_release)} hint="alerts that became a release" />
        <Stat label="Median lead" value={hours(s?.median_lead_alert_to_release_h)} hint="alert → weights" />
      </div>
      <Card>
        <CardHeader
          title="Import a past model"
          subtitle="Pulls dated history from HF, GitHub PRs, Reddit, arXiv and YouTube so the replay has something to replay. Scoring is rule-based, no LLM, so hindsight cannot leak in."
        />
        <div className="flex flex-wrap items-center gap-2 px-4 pb-4">
          <Input placeholder="Model name, e.g. Wan 2.2" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs" />
          <Button size="md" onClick={backfill} disabled={job?.running}>
            <History size={13} /> {job?.running ? 'Importing…' : 'Backfill'}
          </Button>
          {job && !job.running && job.result != null && (
            <span className="text-[12px] text-ink-3">Last import: {JSON.stringify(job.result)}</span>
          )}
          {job?.error && <span className="text-[12px] text-danger">{job.error}</span>}
        </div>
      </Card>
      <Card>
        <CardHeader title="Replay per model" subtitle="Would the radar have caught it, and how early? Click a model on the Radar tab for its chart." />
        <div className="overflow-x-auto px-4 pb-4">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-ink-3">
                <th className="py-2">Model</th>
                <th>Alert</th>
                <th>Weights</th>
                <th>1st competitor</th>
                <th>You</th>
                <th>Lead → weights</th>
                <th>Lead → 1st video</th>
              </tr>
            </thead>
            <tbody>
              {(q.data?.rows ?? []).map((r) => (
                <tr key={r.slug} className="border-t border-border">
                  <td className="py-2 font-medium text-ink">{r.name}</td>
                  <td className={r.caught_before_release ? 'text-ok' : 'text-ink-2'}>{date(r.alert)}</td>
                  <td>{date(r.release)}</td>
                  <td>{date(r.first_competitor_video)}</td>
                  <td>
                    {date(r.own_upload)}
                    {r.own_rank ? ` (#${r.own_rank})` : ''}
                  </td>
                  <td className="tabular-nums">{hours(r.lead_alert_to_release_h)}</td>
                  <td className="tabular-nums">{hours(r.lead_alert_to_first_video_h)}</td>
                </tr>
              ))}
              {q.data && q.data.rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-ink-3">
                    No released models yet. Backfill a few you covered (e.g. "LTX 2.3", "Wan 2.2") to start.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
