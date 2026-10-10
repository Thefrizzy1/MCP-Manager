import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCheck, ChevronDown, ChevronRight, ExternalLink, PartyPopper } from 'lucide-react'
import { api } from '@/lib/api'
import { Drawer } from '@/components/ui/Drawer'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { Section } from './bits'
import { Thumb } from './Ideas'
import { date, type Idea } from './types'

interface DoneResp {
  suggestions: {
    ideas: { idea_id: number; idea_title: string; candidate: string; video_title: string; video_url: string; video_ts: number; reason: string }[]
    candidates: { slug: string; name: string; video: string }[]
    own_videos_seen: number
  }
  finished: {
    ideas: Idea[]
    candidates: { slug: string; name: string; hot: number; stage: string; updated: number }[]
  }
}

function useDone() {
  return useQuery({
    queryKey: ['trend-done'],
    queryFn: () => api.get<DoneResp>('/api/v1/trends/done'),
    refetchInterval: 30000,
  })
}

/** "These look finished" — your uploads matched against open ideas and models. */
export function DoneBanner({ onOpenFinished }: { onOpenFinished: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useDone()
  const [open, setOpen] = useState(false)
  const s = q.data?.suggestions
  const n = (s?.ideas.length ?? 0) + (s?.candidates.length ?? 0)
  const finishedCount = (q.data?.finished.ideas.length ?? 0) + (q.data?.finished.candidates.length ?? 0)

  async function apply(body: { idea_ids?: number[]; slugs?: string[] } = {}) {
    try {
      const r = await api.post<{ ideas: number; candidates: number }>('/api/v1/trends/done/apply', body)
      toast.success(`Done: ${r.ideas} ideas, ${r.candidates} models`)
      qc.invalidateQueries({ queryKey: ['trend-done'] })
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <div className="mb-4 rounded-[var(--radius)] border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
        <button className="flex items-center gap-1.5 text-left" onClick={() => setOpen(!open)} disabled={!n}>
          {n ? open ? <ChevronDown size={14} className="text-ink-3" /> : <ChevronRight size={14} className="text-ink-3" /> : null}
          <CheckCheck size={15} className={n ? 'text-ok' : 'text-ink-3'} />
          <span className="text-[13px] text-ink">
            {n
              ? `${n} look${n === 1 ? 's' : ''} finished: matched against your uploads`
              : `Nothing looks finished (${s?.own_videos_seen ?? 0} of your uploads seen)`}
          </span>
        </button>
        <div className="ml-auto flex items-center gap-2">
          {n > 0 && (
            <Button size="sm" variant="primary" onClick={() => apply({})}>
              Apply to all
            </Button>
          )}
          <Button size="sm" onClick={onOpenFinished}>
            Finished ({finishedCount})
          </Button>
        </div>
      </div>
      {open && n > 0 && (
        <ul className="border-t border-border px-4 py-2">
          {s?.ideas.map((x) => (
            <li key={`i${x.idea_id}`} className="flex items-center gap-3 py-1.5 text-[12.5px]">
              <div className="min-w-0 flex-1">
                <div className="truncate text-ink">{x.idea_title}</div>
                <div className="truncate text-[11.5px] text-ink-3">
                  ←{' '}
                  <a href={x.video_url} target="_blank" rel="noreferrer" className="hover:underline">
                    {x.video_title}
                  </a>{' '}
                  · {date(x.video_ts)} · {x.reason}
                </div>
              </div>
              <Button size="sm" onClick={() => apply({ idea_ids: [x.idea_id], slugs: [] })}>
                Mark done
              </Button>
            </li>
          ))}
          {s?.candidates.map((c) => (
            <li key={`c${c.slug}`} className="flex items-center gap-3 py-1.5 text-[12.5px]">
              <div className="min-w-0 flex-1">
                <div className="truncate text-ink">Model: {c.name}</div>
                <div className="truncate text-[11.5px] text-ink-3">← {c.video}</div>
              </div>
              <Button size="sm" onClick={() => apply({ idea_ids: [], slugs: [c.slug] })}>
                Mark covered
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function FinishedDrawer({ open, onClose, onOpenIdea }: { open: boolean; onClose: () => void; onOpenIdea: (id: number) => void }) {
  const q = useDone()
  const f = q.data?.finished
  return (
    <Drawer open={open} onClose={onClose} title="Finished">
      {!f || (f.ideas.length === 0 && f.candidates.length === 0) ? (
        <div className="py-10 text-center text-[13px] text-ink-3">
          <PartyPopper size={20} className="mx-auto mb-2" />
          Published ideas and covered models collect here.
        </div>
      ) : (
        <>
          {f.ideas.length > 0 && (
            <Section title={`Published ideas (${f.ideas.length})`}>
              <div className="space-y-2">
                {f.ideas.map((i) => (
                  <button
                    key={i.id}
                    onClick={() => onOpenIdea(i.id)}
                    className="flex w-full gap-3 rounded-md border border-border p-2 text-left hover:bg-surface-hover"
                  >
                    <Thumb idea={i} className="w-28 shrink-0 rounded" />
                    <div className="min-w-0">
                      <div className="line-clamp-2 text-[13px] font-medium text-ink">{i.title}</div>
                      <div className="text-[11.5px] text-ink-3">
                        {i.candidate} · {date(i.updated)}
                      </div>
                      {i.archive_reason && <div className="truncate text-[11px] text-ok">{i.archive_reason}</div>}
                    </div>
                  </button>
                ))}
              </div>
            </Section>
          )}
          {f.candidates.length > 0 && (
            <Section title={`Covered models (${f.candidates.length})`}>
              <ul className="space-y-1 text-[12.5px]">
                {f.candidates.map((c) => (
                  <li key={c.slug} className="flex justify-between gap-2">
                    <span className="text-ink">{c.name}</span>
                    <span className="text-ink-3">
                      {c.stage} · {date(c.updated)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <a
            className="mt-2 inline-flex items-center gap-1 text-[12px] text-accent hover:underline"
            href="https://studio.youtube.com"
            target="_blank"
            rel="noreferrer"
          >
            YouTube Studio <ExternalLink size={11} />
          </a>
        </>
      )}
    </Drawer>
  )
}
