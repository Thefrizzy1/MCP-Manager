import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, RefreshCw, Satellite } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { PageHead, PageBody } from '@/components/PageHead'
import { Button } from '@/components/ui/Button'
import { Stat } from '@/components/ui/Stat'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'
import { ActionTag, HotBar, StageTrack } from '@/components/trends/bits'
import { CandidateDrawer } from '@/components/trends/CandidateDrawer'
import { IdeasBoard, IdeaModal } from '@/components/trends/Ideas'
import { DoneBanner, FinishedDrawer } from '@/components/trends/Finished'
import { StudioPanel } from '@/components/trends/Studio'
import { BacktestPanel, ResearchPanel } from '@/components/trends/Research'
import { SettingsPanel } from '@/components/trends/Settings'
import { ago, type Candidate, type Overview } from '@/components/trends/types'

const TABS = [
  ['radar', 'Radar'],
  ['ideas', 'Ideas'],
  ['studio', 'Studio'],
  ['research', 'Research'],
  ['backtest', 'Backtest'],
  ['settings', 'Settings'],
] as const
type Tab = (typeof TABS)[number][0]

// Radar sections, in the order you act on them. Lower ones start collapsed.
const GROUPS: { key: string; label: string; match: (c: Candidate) => boolean; open: boolean }[] = [
  { key: 'prep', label: 'Prep now: code is public, weights are not', match: (c) => c.action === 'PREP NOW', open: true },
  { key: 'shoot', label: 'Shoot now: runnable, window open', match: (c) => c.action.startsWith('SHOOT'), open: true },
  { key: 'watch', label: 'Watching', match: (c) => c.action === 'WATCH', open: true },
  { key: 'late', label: 'Late or done', match: (c) => c.action === 'LATE' || c.action === 'DONE', open: false },
]

function RadarTable({ rows, threshold, onOpen }: { rows: Candidate[]; threshold: number; onOpen: (s: string) => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>(Object.fromEntries(GROUPS.map((g) => [g.key, g.open])))
  return (
    <div className="overflow-hidden rounded-[var(--radius)] border border-border bg-surface">
      <table className="w-full text-[13px]">
        <thead className="bg-surface-2 text-left text-[11px] uppercase tracking-wide text-ink-3">
          <tr>
            <th className="px-4 py-2">Model</th>
            <th className="px-2 py-2">Hot</th>
            <th className="px-2 py-2">Stage</th>
            <th className="px-2 py-2">Action</th>
            <th className="hidden px-2 py-2 md:table-cell">Coverage 10d</th>
            <th className="hidden px-2 py-2 md:table-cell">Signals</th>
            <th className="hidden px-2 py-2 lg:table-cell">First seen</th>
          </tr>
        </thead>
        {GROUPS.map((g) => {
          const items = rows.filter(g.match)
          if (!items.length) return null
          return (
            <tbody key={g.key}>
              <tr className="border-t border-border bg-surface-2/60">
                <td colSpan={7} className="px-3 py-1.5">
                  <button className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-2" onClick={() => setOpen({ ...open, [g.key]: !open[g.key] })}>
                    {open[g.key] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    {g.label}
                    <span className="font-normal text-ink-3">({items.length})</span>
                  </button>
                </td>
              </tr>
              {open[g.key] &&
                items.map((c) => (
                  <tr key={c.slug} className="cursor-pointer border-t border-border hover:bg-surface-hover" onClick={() => onOpen(c.slug)}>
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-ink">
                        {c.status === 'pinned' && <span className="mr-1 text-accent">●</span>}
                        {c.name}
                      </div>
                      <div className="text-[11px] text-ink-3">{c.slug}</div>
                    </td>
                    <td className="px-2">
                      <HotBar hot={c.hot} threshold={threshold} />
                    </td>
                    <td className="px-2">
                      <StageTrack stage={c.stage} />
                    </td>
                    <td className="px-2">
                      <ActionTag action={c.action} />
                    </td>
                    <td className="hidden px-2 text-[12px] tabular-nums text-ink-2 md:table-cell">
                      {c.score.creator_videos_10d ?? 0} · <span title="low-VRAM takes">{c.score.lowvram_videos_10d ?? 0} low-VRAM</span>
                    </td>
                    <td className="hidden px-2 tabular-nums text-ink-2 md:table-cell">{c.signal_count ?? 0}</td>
                    <td className="hidden px-2 text-[12px] text-ink-3 lg:table-cell">{ago(c.first_ts)}</td>
                  </tr>
                ))}
            </tbody>
          )
        })}
      </table>
    </div>
  )
}

export function Trends() {
  const qc = useQueryClient()
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('radar')
  const [slug, setSlug] = useState<string | null>(null)
  const [ideaId, setIdeaId] = useState<number | null>(null)
  const [finishedOpen, setFinishedOpen] = useState(false)
  const [showArchived, setShowArchived] = useState(false)

  const ov = useQuery({
    queryKey: ['trend-overview'],
    queryFn: () => api.get<Overview>('/api/v1/trends/overview'),
    refetchInterval: (q) => (Object.values(q.state.data?.jobs ?? {}).some((j) => j.running) ? 3000 : 20000),
  })
  const d = ov.data
  const collecting = d?.jobs.collect?.running
  const lastRun = d?.runs[0]
  const sources = useMemo(() => Object.entries(lastRun?.detail.sources ?? {}), [lastRun])

  async function collect() {
    try {
      await api.post('/api/v1/trends/collect', {})
      toast.info('Collecting. Big passes (YouTube) take a few minutes.')
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <>
      <PageHead
        title="Trends"
        subtitle="Catch new models before the crowd, turn them into ideas, and prove it would have worked."
        actions={
          <Button variant="primary" onClick={collect} disabled={collecting}>
            <RefreshCw size={14} className={cn(collecting && 'animate-spin')} /> {collecting ? 'Collecting…' : 'Collect now'}
          </Button>
        }
      />
      <div className="flex gap-1 border-b border-border px-6">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={cn(
              '-mb-px border-b-2 px-3 py-2.5 text-[13px] font-medium',
              tab === k ? 'border-accent text-ink' : 'border-transparent text-ink-3 hover:text-ink',
            )}
          >
            {label}
            {k === 'ideas' && d?.stats.ideas ? <span className="ml-1.5 text-[11px] text-ink-3">{d.stats.ideas}</span> : null}
          </button>
        ))}
      </div>
      <PageBody>
        {tab === 'radar' && (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Tracked models" value={d?.stats.tracked ?? '–'} />
              <Stat label="Prep now" value={d?.stats.prep ?? '–'} tone={d?.stats.prep ? 'warn' : 'muted'} hint="code public, weights not yet" />
              <Stat label="Shoot now" value={d?.stats.shoot ?? '–'} tone={d?.stats.shoot ? 'ok' : 'muted'} hint="runnable, window open" />
              <Stat
                label="Next collect"
                value={<span className="text-[16px]">{d?.schedules.collect?.next ? new Date(d.schedules.collect.next).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'off'}</span>}
                hint={lastRun ? `last ${ago(lastRun.started)}` : 'never ran'}
              />
            </div>
            {sources.length > 0 && (
              <div className="mb-4 flex flex-wrap gap-1.5">
                {sources.map(([name, s]) => (
                  <span
                    key={name}
                    title={s.ok ? `${s.seen} seen, ${s.matched} matched, ${s.secs}s` : s.error}
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[11px]',
                      s.ok ? 'border-border text-ink-2' : 'border-danger/40 bg-danger-weak text-danger',
                    )}
                  >
                    {name} {s.ok ? `${s.matched}/${s.seen}` : 'failed'}
                  </span>
                ))}
              </div>
            )}
            {d && d.candidates.length === 0 ? (
              <EmptyState
                icon={Satellite}
                title="Nothing on the radar yet"
                hint="Press Collect now. Add competitor channels and your own in Settings so coverage and 'done' work."
              />
            ) : (
              <RadarTable rows={d?.candidates ?? []} threshold={d?.stats.threshold ?? 70} onOpen={setSlug} />
            )}
          </>
        )}
        {tab === 'ideas' && (
          <>
            <DoneBanner onOpenFinished={() => setFinishedOpen(true)} />
            <IdeasBoard
              ideas={d?.ideas ?? []}
              candidates={d?.candidates ?? []}
              onOpen={setIdeaId}
              showArchived={showArchived}
              setShowArchived={setShowArchived}
            />
          </>
        )}
        {tab === 'studio' && <StudioPanel />}
        {tab === 'research' && <ResearchPanel />}
        {tab === 'backtest' && <BacktestPanel job={d?.jobs.backfill} />}
        {tab === 'settings' && <SettingsPanel />}
      </PageBody>
      <CandidateDrawer slug={slug} onClose={() => setSlug(null)} onOpenIdea={setIdeaId} threshold={d?.stats.threshold ?? 70} />
      <IdeaModal id={ideaId} onClose={() => setIdeaId(null)} />
      <FinishedDrawer open={finishedOpen} onClose={() => setFinishedOpen(false)} onOpenIdea={setIdeaId} />
    </>
  )
}
