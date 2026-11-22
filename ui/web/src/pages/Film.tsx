import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Check } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead } from '@/components/PageHead'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/cn'

interface Job {
  id: string
  title: string
  script: string
  archived: boolean
}
interface Script {
  job: string
  file: string
  sections: { title: string; text: string }[]
}

function jobFromHash(): string {
  const q = window.location.hash.split('?')[1] ?? ''
  return new URLSearchParams(q).get('job') ?? ''
}

/** Read-off view for filming: one section at a time, large, with what is done ticked off. */
export function Film() {
  const [job, setJob] = useState(jobFromHash)
  const [at, setAt] = useState(0)
  const [done, setDone] = useState<Record<string, number[]>>(() => {
    try {
      return JSON.parse(localStorage.getItem('film-done') ?? '{}')
    } catch {
      return {}
    }
  })
  const [size, setSize] = useState(34)
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<{ jobs: Job[] }>('/api/v1/jobs') })
  const script = useQuery({
    queryKey: ['film', job],
    queryFn: () => api.get<Script>(`/api/v1/studio/script?job=${encodeURIComponent(job)}`),
    enabled: !!job,
  })
  const ready = (jobs.data?.jobs ?? []).filter((j) => !j.archived && j.script)
  const sections = script.data?.sections ?? []
  const cur = sections[Math.min(at, Math.max(0, sections.length - 1))]
  const ticked = done[job] ?? []

  function tick() {
    const next = ticked.includes(at) ? ticked.filter((i) => i !== at) : [...ticked, at]
    const all = { ...done, [job]: next }
    setDone(all)
    try {
      localStorage.setItem('film-done', JSON.stringify(all))
    } catch {
      /* private window: progress just does not persist */
    }
    if (!ticked.includes(at) && at < sections.length - 1) setAt(at + 1)
  }

  return (
    <>
      <PageHead
        title="Film"
        actions={
          <>
            <select
              aria-label="Video"
              value={job}
              onChange={(e) => {
                setJob(e.target.value)
                setAt(0)
              }}
              className="h-8 max-w-[280px] rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 text-[13px] text-ink"
            >
              <option value="">Choose a video…</option>
              {ready.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.title}
                </option>
              ))}
            </select>
            <Button size="sm" onClick={() => setSize((s) => Math.max(20, s - 4))} aria-label="Smaller text">
              A−
            </Button>
            <Button size="sm" onClick={() => setSize((s) => Math.min(72, s + 4))} aria-label="Larger text">
              A+
            </Button>
          </>
        }
      />
      {!job ? (
        <p className="p-10 text-center text-[14px] text-ink-3">Choose a video with a script to read it off here.</p>
      ) : !cur ? (
        <p className="p-10 text-center text-[14px] text-ink-3">{script.isLoading ? 'Loading…' : 'This video has no script or filming document yet.'}</p>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className="w-[220px] shrink-0 overflow-y-auto border-r border-border p-2">
            {sections.map((s, i) => (
              <button
                key={i}
                onClick={() => setAt(i)}
                className={cn(
                  'mb-0.5 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-left text-[12.5px]',
                  i === at ? 'bg-accent-weak text-ink' : 'text-ink-2 hover:bg-surface-hover',
                )}
              >
                <span className={cn('flex h-4 w-4 shrink-0 items-center justify-center rounded-full border', ticked.includes(i) ? 'border-ok bg-ok text-white' : 'border-border-strong')}>
                  {ticked.includes(i) && <Check size={10} />}
                </span>
                <span className="truncate">{s.title || `Part ${i + 1}`}</span>
              </button>
            ))}
            <div className="px-2.5 pt-2 text-[11px] text-ink-3">
              {ticked.length} of {sections.length} filmed · {script.data?.file}
            </div>
          </aside>
          <section className="flex min-w-0 flex-1 flex-col">
            <div className="flex-1 overflow-y-auto px-10 py-8">
              <div className="mx-auto max-w-[900px]">
                {cur.title && <div className="mb-6 text-[15px] font-semibold uppercase tracking-wide text-ink-3">{cur.title}</div>}
                <div className="whitespace-pre-wrap font-medium text-ink" style={{ fontSize: size, lineHeight: 1.5 }}>
                  {cur.text.replace(/\n{2,}/g, '\n\n')}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-center gap-3 border-t border-border py-3">
              <Button onClick={() => setAt(Math.max(0, at - 1))} disabled={at === 0}>
                <ChevronLeft size={15} /> Back
              </Button>
              <Button variant="primary" onClick={tick}>
                <Check size={15} /> {ticked.includes(at) ? 'Filmed (undo)' : 'Filmed, next'}
              </Button>
              <Button onClick={() => setAt(Math.min(sections.length - 1, at + 1))} disabled={at >= sections.length - 1}>
                Next <ChevronRight size={15} />
              </Button>
            </div>
          </section>
        </div>
      )}
    </>
  )
}
