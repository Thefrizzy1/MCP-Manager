import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ChevronDown, ChevronRight, Copy, Image as ImageIcon, Lightbulb, Star, Clapperboard, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { EmptyState } from '@/components/ui/EmptyState'
import { useToast } from '@/components/ui/Toast'
import { Section } from './bits'
import { ago, type Candidate, type Idea } from './types'

const STATUS_TONE: Record<string, string> = {
  new: 'text-ink-2',
  shortlisted: 'text-accent',
  producing: 'text-warn',
  published: 'text-ok',
  archived: 'text-ink-3',
}

function Thumb({ idea, className }: { idea: Idea; className?: string }) {
  if (!idea.has_thumb && !idea.thumb_svg) {
    return (
      <div className={cn('flex aspect-video items-center justify-center bg-surface-2 text-ink-3', className)}>
        <div className="px-3 text-center">
          <ImageIcon size={18} className="mx-auto mb-1" />
          <div className="text-[12px] font-semibold text-ink-2">{idea.thumb_text || 'No mock yet'}</div>
        </div>
      </div>
    )
  }
  // An <img> pointing at the server's SVG: agent-written markup can draw, never run.
  return (
    <img
      src={`/api/v1/trends/ideas/${idea.id}/thumb.svg?v=${idea.updated}`}
      alt={idea.thumb_text || idea.title}
      className={cn('aspect-video w-full bg-surface-2 object-cover', className)}
      loading="lazy"
    />
  )
}

export { Thumb }

export function IdeasBoard({
  ideas,
  candidates,
  onOpen,
  showArchived,
  setShowArchived,
}: {
  ideas: Idea[]
  candidates: Candidate[]
  onOpen: (id: number) => void
  showArchived: boolean
  setShowArchived: (v: boolean) => void
}) {
  const archived = useQuery({
    queryKey: ['trend-ideas-archived'],
    queryFn: () => api.get<{ ideas: Idea[] }>('/api/v1/trends/ideas?status=archived'),
    enabled: showArchived,
  })
  const names = useMemo(() => Object.fromEntries(candidates.map((c) => [c.slug, c])), [candidates])
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const toggle = (slug: string) =>
    setCollapsed((s) => {
      const n = new Set(s)
      if (n.has(slug)) n.delete(slug)
      else n.add(slug)
      return n
    })
  const list = useMemo(() => (showArchived ? (archived.data?.ideas ?? []) : ideas), [showArchived, archived.data, ideas])
  // Bunched by model: several ideas on one release read as one decision.
  const groups = useMemo(() => {
    const m = new Map<string, Idea[]>()
    for (const i of list) m.set(i.candidate || '(no model)', [...(m.get(i.candidate || '(no model)') ?? []), i])
    return [...m.entries()].sort(
      (a, b) => Math.max(...b[1].map((i) => i.hot + 4 * (i.catches - 1))) - Math.max(...a[1].map((i) => i.hot + 4 * (i.catches - 1))),
    )
  }, [list])

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Button size="sm" variant={showArchived ? 'default' : 'ghost'} onClick={() => setShowArchived(!showArchived)}>
          <Archive size={13} /> {showArchived ? 'Back to active' : 'Show archive'}
        </Button>
        <span className="text-[12px] text-ink-3">
          Ideas on the same model and angle merge automatically; each repeat is a "catch" and raises the hot score.
        </span>
      </div>
      {list.length === 0 ? (
        <EmptyState
          icon={Lightbulb}
          title={showArchived ? 'Nothing archived' : 'No ideas yet'}
          hint="Run the Idea Studio (Studio tab) or ask Claude over Plutus MCP to save one with trend_radar_save_idea."
        />
      ) : (
        groups.map(([slug, items]) => (
          <div key={slug} className="mb-5">
            <button className="mb-2 flex w-full items-baseline gap-2 text-left" onClick={() => toggle(slug)}>
              {collapsed.has(slug) ? <ChevronRight size={14} className="self-center text-ink-3" /> : <ChevronDown size={14} className="self-center text-ink-3" />}
              <h3 className="text-[14px] font-semibold text-ink">{names[slug]?.name ?? slug}</h3>
              <span className="text-[11.5px] text-ink-3">
                {items.length} idea{items.length === 1 ? '' : 's'}
                {names[slug] ? ` · ${names[slug].action} · hot ${Math.round(names[slug].hot)}` : ''}
              </span>
            </button>
            {!collapsed.has(slug) && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((i) => (
                <button
                  key={i.id}
                  onClick={() => onOpen(i.id)}
                  className="overflow-hidden rounded-[var(--radius)] border border-border bg-surface text-left transition-colors hover:border-border-strong"
                >
                  <Thumb idea={i} />
                  <div className="p-3">
                    <div className="line-clamp-2 text-[13.5px] font-semibold leading-snug text-ink">{i.title}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-ink-3">
                      <span className="font-semibold tabular-nums text-ink-2">hot {Math.round(i.hot)}</span>
                      {i.catches > 1 && <span className="text-accent">caught {i.catches}×</span>}
                      <span className={STATUS_TONE[i.status]}>{i.status}</span>
                      <span>{ago(i.updated)}</span>
                    </div>
                    {i.archive_reason && <div className="mt-1 text-[11px] text-ink-3">{i.archive_reason}</div>}
                  </div>
                </button>
              ))}
            </div>
            )}
          </div>
        ))
      )}
    </div>
  )
}

function CopyBlock({ label, text }: { label: string; text?: string }) {
  const toast = useToast()
  if (!text) return null
  return (
    <Section
      title={label}
      action={
        <Button
          size="sm"
          variant="ghost"
          onClick={() => navigator.clipboard?.writeText(text).then(() => toast.success('Copied'), () => toast.error('Copy failed'))}
        >
          <Copy size={12} /> Copy
        </Button>
      }
    >
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-3 font-[inherit] text-[12.5px] leading-relaxed text-ink">
        {text}
      </pre>
    </Section>
  )
}

export function IdeaModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [editTitle, setEditTitle] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const q = useQuery({
    queryKey: ['trend-idea', id],
    queryFn: () => api.get<{ idea: Idea }>(`/api/v1/trends/ideas/${id}`),
    enabled: id !== null,
  })
  const i = q.data?.idea

  async function update(body: Record<string, string>, msg: string) {
    if (id === null) return
    try {
      await api.post(`/api/v1/trends/ideas/${id}`, body)
      toast.success(msg)
      qc.invalidateQueries({ queryKey: ['trend-overview'] })
      qc.invalidateQueries({ queryKey: ['trend-ideas-archived'] })
      q.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function uploadSvg(f: File | undefined) {
    if (!f) return
    const text = await f.text()
    update({ thumb_svg: text }, 'Thumbnail replaced')
  }

  return (
    <Modal
      open={id !== null}
      onClose={() => {
        setEditTitle(null)
        onClose()
      }}
      title={i ? `Idea #${i.id}` : 'Idea'}
      width={860}
      footer={
        i && (
          <div className="flex w-full flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => update({ status: 'shortlisted' }, 'Shortlisted')}>
              <Star size={13} /> Shortlist
            </Button>
            <Button size="sm" onClick={() => update({ status: 'producing' }, 'Marked producing')}>
              <Clapperboard size={13} /> Producing
            </Button>
            <Button size="sm" onClick={() => update({ status: 'published' }, 'Marked published')}>
              Published
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <Input placeholder="why archive?" value={reason} onChange={(e) => setReason(e.target.value)} className="w-48" />
              <Button
                size="sm"
                variant="danger"
                onClick={() => update({ status: 'archived', archive_reason: reason || 'archived by hand' }, 'Archived')}
              >
                <Archive size={13} /> Archive
              </Button>
            </div>
          </div>
        )
      }
    >
      {!i ? (
        <div className="text-[13px] text-ink-3">Loading…</div>
      ) : (
        <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div>
            <div className="overflow-hidden rounded-md border border-border">
              <Thumb idea={i} />
            </div>
            <label className="mt-2 inline-flex cursor-pointer items-center gap-1.5 text-[11.5px] text-ink-3 hover:text-ink">
              <Upload size={12} /> Replace with your own SVG
              <input type="file" accept=".svg,image/svg+xml" className="hidden" onChange={(e) => uploadSvg(e.target.files?.[0])} />
            </label>
            {i.thumb_text && (
              <div className="mt-3 text-[12.5px]">
                <span className="text-ink-3">Thumbnail text: </span>
                <b className="text-ink">{i.thumb_text}</b>
              </div>
            )}
            {i.thumb_notes && <p className="mt-1 text-[12px] text-ink-2">{i.thumb_notes}</p>}
            {i.why_now && (
              <Section title="Why now">
                <p className="text-[12.5px] text-ink">{i.why_now}</p>
              </Section>
            )}
            {i.evidence && i.evidence.length > 0 && (
              <Section title="Evidence">
                <ul className="space-y-1 text-[12px]">
                  {i.evidence.map((e) => (
                    <li key={e} className="truncate">
                      <a href={e} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                        {e}
                      </a>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
          </div>
          <div>
            {editTitle === null ? (
              <h2
                className="cursor-text text-[18px] font-bold leading-snug text-ink"
                title="Click to edit"
                onClick={() => setEditTitle(i.title)}
              >
                {i.title}
              </h2>
            ) : (
              <div className="flex gap-2">
                <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    update({ title: editTitle }, 'Title saved')
                    setEditTitle(null)
                  }}
                >
                  Save
                </Button>
              </div>
            )}
            <div className="mt-1 text-[12px] text-ink-3">
              {i.candidate} · hot {Math.round(i.hot)} · caught {i.catches}× · <span className={STATUS_TONE[i.status]}>{i.status}</span>
            </div>
            {i.alt_titles.length > 0 && (
              <Section title="Alternative titles">
                <ul className="space-y-1">
                  {i.alt_titles.map((t) => (
                    <li key={t}>
                      <button className="text-left text-[13px] text-ink-2 hover:text-accent" onClick={() => update({ title: t }, 'Title swapped')}>
                        {t}
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            {i.angle && (
              <Section title="Angle">
                <p className="text-[13px] text-ink">{i.angle}</p>
              </Section>
            )}
            <CopyBlock label="Hook (first 15 s)" text={i.hook} />
            <CopyBlock label="Description" text={i.description} />
            <CopyBlock label="Script" text={i.script} />
            <CopyBlock label="What worked for others" text={i.research} />
          </div>
        </div>
      )}
    </Modal>
  )
}
