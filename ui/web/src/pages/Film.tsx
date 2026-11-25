import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Check, MonitorPlay, Pause, Play, RotateCcw, SkipBack, SkipForward, Upload, X } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead } from '@/components/PageHead'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'

interface Job {
  id: string
  title: string
  script: string
  archived: boolean
}
interface PrompterState {
  uploads: { id: string; name: string; job: string; note: string }[]
  screen: { on: boolean; title?: string; playing?: boolean; percent?: number; part?: string; speed?: number; size?: number }
}
interface Script {
  file?: string
  title?: string
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
  const toast = useToast()
  const qc = useQueryClient()
  const picker = useRef<HTMLInputElement>(null)
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<{ jobs: Job[] }>('/api/v1/jobs') })
  const prompter = useQuery({ queryKey: ['prompter'], queryFn: () => api.get<PrompterState>('/api/v1/prompter'), refetchInterval: 4000 })
  const uploads = prompter.data?.uploads ?? []
  const screen = prompter.data?.screen
  // What is picked: a video folder, or an uploaded file as "upload:<id>".
  const source = job.startsWith('upload:') ? job : `job:${job}`
  const script = useQuery({
    queryKey: ['film', job],
    queryFn: () => api.get<Script>(`/api/v1/prompter/script?source=${encodeURIComponent(source)}`),
    enabled: !!job,
    retry: false,
  })

  async function send(cmd: string) {
    try {
      await api.post('/api/v1/prompter/command', { cmd, source: cmd === 'load' ? source : '' })
      if (cmd === 'load') toast.success(screen?.on ? 'On the prompter.' : 'Ready. Open the prompter on the tablet.')
      setTimeout(() => prompter.refetch(), 400)
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function upload(file: File | undefined) {
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    body.append('job', job.startsWith('upload:') ? '' : job)
    try {
      const r = await fetch('/api/v1/prompter/upload', { method: 'POST', body })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || r.statusText)
      await qc.invalidateQueries({ queryKey: ['prompter'] })
      setJob(`upload:${d.id}`)
      setAt(0)
      toast.success(d.note || `Added ${d.name}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      if (picker.current) picker.current.value = ''
    }
  }
  async function removeUpload() {
    try {
      await api.del(`/api/v1/prompter/upload/${job.slice(7)}`)
      setJob('')
      qc.invalidateQueries({ queryKey: ['prompter'] })
    } catch (e) {
      toast.error(String(e))
    }
  }
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
              {uploads.length > 0 && (
                <optgroup label="Uploaded">
                  {uploads.map((u) => (
                    <option key={u.id} value={`upload:${u.id}`}>
                      {u.name}
                    </option>
                  ))}
                </optgroup>
              )}
              <optgroup label="Videos">
                {ready.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title}
                  </option>
                ))}
              </optgroup>
            </select>
            {job.startsWith('upload:') && (
              <Button size="icon-sm" variant="ghost" aria-label="Remove this upload" onClick={removeUpload}>
                <X size={14} />
              </Button>
            )}
            <input ref={picker} type="file" accept=".md,.markdown,.txt,.pdf,.docx" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
            <Button size="sm" onClick={() => picker.current?.click()}>
              <Upload size={13} /> Upload script
            </Button>
            <Button size="sm" onClick={() => window.open('#/prompter' + (job ? `?source=${encodeURIComponent(source)}` : ''), '_blank')}>
              <MonitorPlay size={13} /> Open here
            </Button>
            <Button size="sm" variant="primary" disabled={!job} onClick={() => send('load')}>
              <MonitorPlay size={13} /> To the prompter
            </Button>
            <Button size="sm" onClick={() => setSize((s) => Math.max(20, s - 4))} aria-label="Smaller text">
              A−
            </Button>
            <Button size="sm" onClick={() => setSize((s) => Math.min(72, s + 4))} aria-label="Larger text">
              A+
            </Button>
          </>
        }
      />
      {screen?.on && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-2 text-[12.5px] text-ink-2">
          <span className="mr-2 flex min-w-0 items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full bg-ok" />
            <span className="truncate">
              Prompter: {screen.title || 'no script'} · {screen.percent ?? 0}%{screen.part ? ` · ${screen.part}` : ''}
            </span>
          </span>
          <Button size="sm" onClick={() => send('restart')}><SkipBack size={13} /> Start</Button>
          <Button size="sm" onClick={() => send('back')}><RotateCcw size={13} /> Replay part</Button>
          <Button size="sm" variant="primary" onClick={() => send('toggle')}>
            {screen.playing ? <Pause size={13} /> : <Play size={13} />} {screen.playing ? 'Pause' : 'Play'}
          </Button>
          <Button size="sm" onClick={() => send('next')}><SkipForward size={13} /> Next</Button>
          <Button size="sm" onClick={() => send('smaller')} aria-label="Smaller prompter text">A−</Button>
          <Button size="sm" onClick={() => send('bigger')} aria-label="Bigger prompter text">A+</Button>
          <Button size="sm" onClick={() => send('slower')}>Slower</Button>
          <Button size="sm" onClick={() => send('faster')}>Faster</Button>
          <Button size="sm" onClick={() => send('mirror')}>Mirror</Button>
        </div>
      )}
      {!job ? (
        <p className="p-10 text-center text-[14px] text-ink-3">Choose a video, or upload a .md or PDF script.</p>
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
              {ticked.length} of {sections.length} filmed · {script.data?.file || script.data?.title}
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
