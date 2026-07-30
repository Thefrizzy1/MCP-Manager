import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Wrench, MessageSquare, Brain, Flag, Terminal } from 'lucide-react'
import { api } from '@/lib/api'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'

interface Entry {
  kind: 'session' | 'assistant' | 'thinking' | 'tool_call' | 'tool_result' | 'final'
  text?: string
  name?: string
  id?: string
  input?: string
  is_error?: boolean
  model?: string
  cwd?: string
  tools?: string[]
  mcp_servers?: unknown[]
  cost_usd?: number
  turns?: number
}
interface TranscriptResp {
  id: string
  label?: string
  ok?: boolean
  error?: string
  auth_source?: string
  mcp_services?: string[] | null
  available: boolean
  entries: Entry[]
  log: string[]
}

/** Collapsible block for anything long (tool arguments, tool output). */
function Collapsible({ text, lines = 6 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false)
  const split = text.split('\n')
  const long = split.length > lines || text.length > 400
  const shown = open || !long ? text : split.slice(0, lines).join('\n').slice(0, 400)
  return (
    <div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-surface px-2 py-1.5 font-mono text-[11px] text-ink-2">
        {shown}
        {!open && long && '\n…'}
      </pre>
      {long && (
        <button className="mt-0.5 text-[11px] text-accent hover:underline" onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : `Show all (${split.length} lines)`}
        </button>
      )}
    </div>
  )
}

const ICON = {
  session: Terminal,
  assistant: MessageSquare,
  thinking: Brain,
  tool_call: Wrench,
  tool_result: ChevronRight,
  final: Flag,
} as const

export function RunTranscriptModal({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [showRaw, setShowRaw] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['run-transcript', runId],
    queryFn: () => api.get<TranscriptResp>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/transcript`),
  })

  const toolCalls = (data?.entries ?? []).filter((e) => e.kind === 'tool_call').length

  return (
    <Modal
      open
      onClose={onClose}
      title={`Run — ${data?.label || runId}`}
      width={840}
      footer={
        <>
          <span className="mr-auto text-[12px] text-ink-3">
            {toolCalls} tool call{toolCalls === 1 ? '' : 's'}
            {data?.auth_source && ` · auth: ${data.auth_source}`}
          </span>
          <Button variant="ghost" size="sm" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? 'Transcript' : 'Console log'}
          </Button>
          <Button variant="primary" size="sm" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      {isLoading ? (
        <p className="text-[13px] text-ink-3">Loading…</p>
      ) : showRaw ? (
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded bg-surface-2 px-3 py-2 font-mono text-[11.5px] text-ink-2">
          {(data?.log ?? []).join('\n') || '(no console output)'}
        </pre>
      ) : !data?.available ? (
        <p className="text-[13px] text-ink-3">
          No transcript was recorded for this run — it predates transcript capture. New runs record every
          message and tool call. The console log is still available above.
        </p>
      ) : (
        <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {data.error && (
            <p className="rounded bg-danger/10 px-2.5 py-1.5 text-[12px] text-danger">{data.error}</p>
          )}
          {data.entries.map((e, i) => {
            const Icon = ICON[e.kind] ?? MessageSquare
            return (
              <div key={i} className="rounded-[var(--radius-sm)] border border-border px-2.5 py-2">
                <div className="mb-1 flex items-center gap-1.5">
                  <Icon size={13} className={e.is_error ? 'text-danger' : 'text-ink-3'} />
                  <span className="text-[11px] font-medium uppercase tracking-wide text-ink-3">
                    {e.kind === 'tool_call'
                      ? `tool · ${e.name}`
                      : e.kind === 'tool_result'
                        ? e.is_error
                          ? 'tool result · error'
                          : 'tool result'
                        : e.kind}
                  </span>
                  {e.kind === 'final' && e.cost_usd != null && (
                    <span className="text-[11px] text-ink-3">
                      ${e.cost_usd} · {e.turns} turns
                    </span>
                  )}
                </div>

                {e.kind === 'session' ? (
                  <p className="text-[12px] text-ink-3">
                    {e.model && <>model {e.model} · </>}
                    {e.tools?.length ?? 0} tools available
                    {e.cwd && <> · cwd {e.cwd}</>}
                  </p>
                ) : e.kind === 'tool_call' ? (
                  <Collapsible text={e.input || '{}'} />
                ) : e.kind === 'tool_result' ? (
                  <Collapsible text={e.text || '(empty)'} />
                ) : (
                  <p className="whitespace-pre-wrap text-[12.5px] text-ink-2">{e.text}</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Modal>
  )
}
