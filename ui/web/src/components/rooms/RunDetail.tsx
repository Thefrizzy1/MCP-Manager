import { useQuery } from '@tanstack/react-query'
import { ArrowRight, FolderOpen } from 'lucide-react'

import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import { Button } from '@/components/ui/Button'
import { Drawer } from '@/components/ui/Drawer'
import { StatusDot } from '@/components/ui/StatusDot'

/**
 * What actually happened in one room run, seat by seat.
 *
 * The record has always carried each seat's full output and error — the MCP
 * ``room_result`` tool reads exactly this — but the dashboard only ever showed a
 * row of ticks and crosses, so the one question you have when a nightly room
 * failed at 03:00 ("failed where, and saying what?") had no answer in the UI.
 */
interface RunStep {
  seat_id: string
  role: string
  label: string
  run_id: string
  ok: boolean
  cost_usd: number
  result?: string
  error?: string | null
}
interface RunRecord {
  id: string
  room_id: string
  room_label: string
  brief: string
  folder: string
  started: string
  finished: string | null
  ok: boolean
  cost_usd: number
  steps: RunStep[]
  error?: string | null
  next_run_id?: string
  next_error?: string
}

export function RunDetail({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['room-run', runId],
    queryFn: () => api.get<RunRecord>(`/api/v1/rooms/runs/${runId}`),
    enabled: Boolean(runId),
  })
  const rec = q.data

  return (
    <Drawer
      open={Boolean(runId)}
      onClose={onClose}
      title={rec ? `${rec.room_label} — ${rec.ok ? 'finished' : 'failed'}` : 'Room run'}
    >
      {q.isLoading && <p className="text-[12.5px] text-ink-3">Loading…</p>}
      {q.isError && (
        <p className="text-[12.5px] text-danger">
          Could not load this run. It may have been cleared from the history.
        </p>
      )}

      {rec && (
        <div className="space-y-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
            <dt className="text-ink-3">Started</dt>
            <dd className="text-ink-2">{rec.started}</dd>
            <dt className="text-ink-3">Finished</dt>
            <dd className="text-ink-2">{rec.finished || 'still running'}</dd>
            <dt className="text-ink-3">Cost</dt>
            <dd className="text-ink-2">${rec.cost_usd}</dd>
          </dl>

          {rec.error && (
            <p className="rounded-[var(--radius-sm)] border border-danger/40 bg-danger/5 px-2.5 py-2 text-[12.5px] text-danger">
              {rec.error}
            </p>
          )}

          {rec.folder && (
            <Button variant="default" size="sm" onClick={() => navigate('files')}>
              <FolderOpen size={13} /> {rec.folder}
            </Button>
          )}

          <section>
            <h3 className="mb-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-ink-3">
              Seats, in order
            </h3>
            <ol className="space-y-2">
              {rec.steps.map((s, i) => (
                <li
                  key={`${s.seat_id}-${i}`}
                  className="rounded-[var(--radius-sm)] border border-border bg-surface-2 px-2.5 py-2"
                >
                  <div className="flex items-center gap-2">
                    <StatusDot state={s.ok ? 'online' : 'offline'} />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">
                      {i + 1}. {s.label || s.role}
                    </span>
                    <span className="font-mono text-[11px] text-ink-3">${s.cost_usd}</span>
                  </div>

                  {s.error && (
                    <p className="mt-1 text-[11.5px] text-danger">{s.error}</p>
                  )}

                  {s.result && (
                    // Pre-wrapped and capped: a seat's output is markdown that can
                    // run to thousands of words, and the drawer is for finding out
                    // what happened, not for reading the deliverable — that is in
                    // the working folder.
                    <details className="mt-1">
                      <summary className="cursor-pointer text-[11.5px] text-ink-3 hover:text-ink-2">
                        what it produced
                      </summary>
                      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] bg-surface px-2 py-1.5 font-mono text-[11.5px] leading-snug text-ink-2">
                        {s.result.length > 6000
                          ? `${s.result.slice(0, 6000)}\n\n… truncated — the full output is in the run's transcript.`
                          : s.result}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          </section>

          {(rec.next_run_id || rec.next_error) && (
            <section className="border-t border-border pt-3">
              <h3 className="mb-1 flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-ink-3">
                <ArrowRight size={12} aria-hidden /> Handoff
              </h3>
              {rec.next_error ? (
                <p className="text-[12.5px] text-danger">{rec.next_error}</p>
              ) : (
                <p className="text-[12.5px] text-ink-2">
                  Handed off to run <code className="font-mono text-[11.5px]">{rec.next_run_id}</code>.
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </Drawer>
  )
}
