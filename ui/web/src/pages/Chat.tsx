import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead } from '@/components/PageHead'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { WORKER_RAMP } from '@/components/PixelArt'
import { cn } from '@/lib/cn'

interface Job {
  id: string
  title: string
  current: string
  archived: boolean
}
interface Message {
  id: number
  role: 'user' | 'agent' | 'system'
  agent: string
  text: string
  ts: number
}
interface Task {
  id: number
  kind: string
  agent: string
  status: string
  reason: string
  blocked?: boolean
  waiting?: boolean
}
interface Thread {
  job: string
  messages: Message[]
  tasks: Task[]
}

const NAME: Record<string, string> = {
  manager: 'Channel Manager',
  researcher: 'Researcher',
  writer: 'Writer',
  editor: 'Senior Editor',
  motion: 'Motion Graphics',
  thumbnail: 'Thumbnail Maker',
  shorts: 'Shorts Editor',
  publisher: 'Publisher',
}
const CHANNEL = 'channel'

function jobFromHash(): string {
  const q = window.location.hash.split('?')[1] ?? ''
  return new URLSearchParams(q).get('job') ?? CHANNEL
}

export function Chat() {
  const toast = useToast()
  const qc = useQueryClient()
  const [job, setJob] = useState(jobFromHash)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const end = useRef<HTMLDivElement | null>(null)
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<{ jobs: Job[] }>('/api/v1/jobs') })
  const thread = useQuery({
    queryKey: ['thread', job],
    queryFn: () => api.get<Thread>(`/api/v1/studio/thread?job=${encodeURIComponent(job)}`),
    refetchInterval: 5000,
  })
  const open = (jobs.data?.jobs ?? []).filter((j) => !j.archived)
  const messages = thread.data?.messages ?? []
  const working = (thread.data?.tasks ?? []).filter((t) => t.status === 'queued' || t.status === 'running')

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, working.length, job])

  async function send() {
    const body = text.trim()
    if (!body || sending) return
    setSending(true)
    try {
      qc.setQueryData(['thread', job], await api.post<Thread>('/api/v1/studio/say', { job, text: body }))
      setText('')
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <PageHead title="Chat" />
      <div className="flex min-h-0 flex-1">
        <aside className="w-[230px] shrink-0 overflow-y-auto border-r border-border p-2">
          {[{ id: CHANNEL, title: 'The whole channel', current: '', archived: false }, ...open].map((j) => (
            <button
              key={j.id}
              onClick={() => setJob(j.id)}
              className={cn(
                'mb-0.5 block w-full rounded-[var(--radius-sm)] px-2.5 py-2 text-left',
                job === j.id ? 'bg-accent-weak text-ink' : 'text-ink-2 hover:bg-surface-hover',
              )}
            >
              <div className="truncate text-[13px] font-medium">{j.title}</div>
              {j.current && <div className="text-[11px] text-ink-3">{j.current === 'done' ? 'finished' : `at ${j.current}`}</div>}
            </button>
          ))}
        </aside>
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto px-6 py-4">
            <div className="mx-auto max-w-[760px] space-y-3">
              {messages.length === 0 && (
                <p className="pt-10 text-center text-[13px] text-ink-3">
                  Tell the Channel Manager what you want for this video. It hands the work to the right agent.
                </p>
              )}
              {messages.map((m) =>
                m.role === 'system' ? (
                  <div key={m.id} className="text-center text-[11.5px] text-ink-3">
                    {m.text}
                  </div>
                ) : (
                  <div key={m.id} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
                    <div
                      className={cn(
                        'max-w-[85%] whitespace-pre-wrap rounded-[12px] px-3.5 py-2.5 text-[13.5px] leading-relaxed',
                        m.role === 'user' ? 'bg-accent text-accent-fg' : 'border border-border bg-surface text-ink',
                      )}
                    >
                      {m.role === 'agent' && (
                        <div className="mb-1 text-[11px] font-semibold" style={{ color: WORKER_RAMP[m.agent]?.[0] ?? 'var(--ink-3)' }}>
                          {NAME[m.agent] ?? m.agent}
                        </div>
                      )}
                      {m.text}
                    </div>
                  </div>
                ),
              )}
              {working.map((t) => (
                <div key={t.id} className="flex items-center gap-2 text-[12.5px] text-ink-2">
                  <span className="stage-live h-2 w-2 rounded-full" style={{ background: WORKER_RAMP[t.agent]?.[0] ?? 'var(--accent)' }} />
                  {NAME[t.agent] ?? t.agent}{' '}
                  {t.status === 'running'
                    ? t.kind === 'chat'
                      ? 'is answering'
                      : `is working on ${t.kind.replace('_', ' ')}`
                    : t.blocked
                      ? `cannot start ${t.kind.replace('_', ' ')}: the step before it failed`
                      : t.waiting
                        ? `starts ${t.kind.replace('_', ' ')} when the step before it is done`
                        : t.reason || `has ${t.kind === 'chat' ? 'your message' : t.kind.replace('_', ' ')} next in line`}
                </div>
              ))}
              <div ref={end} />
            </div>
          </div>
          <div className="border-t border-border px-6 py-3">
            <div className="mx-auto flex max-w-[760px] items-end gap-2">
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    send()
                  }
                }}
                placeholder="Write to the Channel Manager…"
                rows={2}
                className="min-h-[44px] flex-1 resize-none rounded-[10px] border border-border-strong bg-surface px-3 py-2.5 text-[13.5px] text-ink"
              />
              <Button variant="primary" onClick={send} disabled={sending || !text.trim()} aria-label="Send">
                <Send size={15} />
              </Button>
            </div>
          </div>
        </section>
      </div>
    </>
  )
}
