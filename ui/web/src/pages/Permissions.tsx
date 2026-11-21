import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { cn } from '@/lib/cn'

interface Action {
  id: string
  group: string
  label: string
  help: string
  default: string
  value: 'allow' | 'ask' | 'never'
}
interface Req {
  id: number
  ts: number
  action: string
  summary: string
  link: string
  status: 'pending' | 'approved' | 'denied' | 'done'
}
interface Overview {
  actions: Action[]
  requests: Req[]
}

const OPTIONS: { value: Action['value']; label: string }[] = [
  { value: 'allow', label: 'Allowed' },
  { value: 'ask', label: 'Ask me' },
  { value: 'never', label: 'Never' },
]

export function Permissions() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api.get<Overview>('/api/v1/permissions'),
    refetchInterval: 30_000,
  })

  async function post(path: string, body: unknown) {
    try {
      qc.setQueryData(['permissions'], await api.post<Overview>(path, body))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const groups = [...new Set((data?.actions ?? []).map((a) => a.group))]
  const pending = (data?.requests ?? []).filter((r) => r.status === 'pending')
  const history = (data?.requests ?? []).filter((r) => r.status !== 'pending').slice(0, 15)

  return (
    <>
      <PageHead
        title="Permissions"
        subtitle="Research and drafts always run. These are the actions that reach outside."
      />
      <PageBody>
        <div className="space-y-5">
          <Card>
            <CardHeader
              title={`Waiting for you (${pending.length})`}
              subtitle="Prepared work that will only happen after you approve it."
            />
            <div className="px-4 pb-3.5">
              {pending.length === 0 && <p className="text-[12.5px] text-ink-3">Nothing is waiting.</p>}
              {pending.map((r) => (
                <div key={r.id} className="flex items-center gap-3 border-t border-border py-2.5 first:border-t-0">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] text-ink">{r.summary}</div>
                    <div className="text-[11.5px] text-ink-3">
                      {r.action}
                      {r.link && (
                        <>
                          {' · '}
                          <a className="text-accent hover:underline" href={r.link} target="_blank" rel="noreferrer">
                            Open
                          </a>
                        </>
                      )}
                    </div>
                  </div>
                  <Button size="sm" variant="primary" onClick={() => post('/api/v1/permissions/decide', { id: r.id, approve: true })}>
                    <Check size={13} /> Approve
                  </Button>
                  <Button size="sm" onClick={() => post('/api/v1/permissions/decide', { id: r.id, approve: false })}>
                    <X size={13} /> Deny
                  </Button>
                </div>
              ))}
            </div>
          </Card>

          {groups.map((g) => (
            <Card key={g}>
              <CardHeader title={g} />
              <div className="px-4 pb-2">
                {data!.actions
                  .filter((a) => a.group === g)
                  .map((a) => (
                    <div key={a.id} className="flex items-center gap-3 border-t border-border py-2.5 first:border-t-0">
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] text-ink">{a.label}</div>
                        <div className="text-[11.5px] text-ink-3">{a.help}</div>
                      </div>
                      <div className="flex overflow-hidden rounded-[var(--radius-sm)] border border-border-strong" role="group" aria-label={a.label}>
                        {OPTIONS.map((o) => (
                          <button
                            key={o.value}
                            aria-pressed={a.value === o.value}
                            onClick={() => post('/api/v1/permissions/set', { action: a.id, value: o.value })}
                            className={cn(
                              'h-7 px-2.5 text-[12px]',
                              a.value === o.value ? 'bg-accent text-accent-fg' : 'bg-surface text-ink-2 hover:bg-surface-hover',
                            )}
                          >
                            {o.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
              </div>
            </Card>
          ))}

          {history.length > 0 && (
            <Card>
              <CardHeader title="Recent decisions" />
              <ul className="space-y-1 px-4 pb-3.5 text-[12.5px] text-ink-2">
                {history.map((r) => (
                  <li key={r.id}>
                    <span className="text-ink-3">{r.status}</span> · {r.summary}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </PageBody>
    </>
  )
}
