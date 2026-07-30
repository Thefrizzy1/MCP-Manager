import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Play, Trash2, GripVertical, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import type { Service } from '@/lib/health'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Select, Textarea } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { StatusDot } from '@/components/ui/StatusDot'
import { useToast } from '@/components/ui/Toast'
import { ConnectionPicker } from '@/components/agents/ConnectionPicker'

interface Seat {
  id: string
  role: string
  label: string
  provider: string
  account_id: string
  goal: string
}
interface Room {
  id: string
  label: string
  brief: string
  mcp_services: string[]
  seats: Seat[]
}
interface RoomStep {
  seat_id: string
  role: string
  label: string
  run_id: string
  ok: boolean
  cost_usd: number
  error?: string | null
}
interface RoomRun {
  id: string
  room_label: string
  started: string
  ok: boolean
  cost_usd: number
  steps: RoomStep[]
  error?: string | null
}
interface Live {
  room_id: string
  run_id: string
  seat_id: string
  running: boolean
}
interface RoomsResp {
  rooms: Room[]
  roles: string[]
  live: Live
  runs: RoomRun[]
}
interface LinkedAccount {
  provider: string
  providerLabel: string
  role: string
  id: string
  label: string
}

const ROLE_HINT: Record<string, string> = {
  manager: 'reviews the work handed to it and directs the next person',
  researcher: 'gathers information and reports findings',
  developer: 'turns accepted findings into working changes',
  reviewer: 'checks the work against the brief',
  writer: 'produces the finished written output',
}

export function Rooms() {
  const toast = useToast()
  const rooms = useQuery({
    queryKey: ['rooms'],
    queryFn: () => api.get<RoomsResp>('/api/v1/rooms'),
    refetchInterval: 4000,
  })
  const conns = useQuery({
    queryKey: ['agent-conns'],
    queryFn: () => api.get<{ services?: Service[] }>('/api/v1/dashboard?sections=services'),
  })
  const providers = useQuery({
    queryKey: ['ai-providers'],
    queryFn: () =>
      api.get<{
        providers: {
          id: string
          label: string
          runnable: boolean
          role_label: string
          accounts: { id: string; label: string; authenticated: boolean }[]
        }[]
      }>('/api/v1/providers'),
  })

  const [newRoom, setNewRoom] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [dragging, setDragging] = useState<LinkedAccount | null>(null)
  const [dragSeat, setDragSeat] = useState<string | null>(null)
  const [busy, setBusy] = useState('')

  const linked: LinkedAccount[] = (providers.data?.providers ?? [])
    .filter((p) => p.runnable)
    .flatMap((p) =>
      p.accounts
        .filter((a) => a.authenticated)
        .map((a) => ({ provider: p.id, providerLabel: p.label, role: p.role_label, id: a.id, label: a.label })),
    )

  const list = rooms.data?.rooms ?? []
  const live = rooms.data?.live
  const open = list.find((r) => r.id === openId) ?? list[0] ?? null
  const services = (conns.data?.services ?? []).filter((s) => s.configured && !s.ignored)

  async function call(fn: () => Promise<unknown>, key: string, ok?: string) {
    setBusy(key)
    try {
      await fn()
      if (ok) toast.success(ok)
      rooms.refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  async function dropIntoRoom(room: Room, acct: LinkedAccount, role: string) {
    await call(
      () =>
        api.post(`/api/v1/rooms/${room.id}/seats`, {
          role,
          provider: acct.provider,
          account_id: acct.id,
          label: `${role[0].toUpperCase()}${role.slice(1)}`,
        }),
      'drop',
      `${acct.label} joined ${room.label}.`,
    )
  }

  async function reorder(room: Room, fromId: string, toId: string) {
    if (fromId === toId) return
    const ids = room.seats.map((s) => s.id)
    const from = ids.indexOf(fromId)
    const to = ids.indexOf(toId)
    if (from < 0 || to < 0) return
    ids.splice(to, 0, ids.splice(from, 1)[0])
    await call(() => api.post(`/api/v1/rooms/${room.id}/order`, { seat_ids: ids }), 'order')
  }

  return (
    <>
      <PageHead
        title="Rooms"
        subtitle="A team of agents working one after another on a shared brief"
        actions={
          <div className="flex items-center gap-2">
            <Input
              className="max-w-[190px]"
              value={newRoom}
              placeholder="Room name"
              onChange={(e) => setNewRoom(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newRoom.trim()) {
                  call(() => api.post('/api/v1/rooms', { label: newRoom.trim() }), 'new').then(() =>
                    setNewRoom(''),
                  )
                }
              }}
            />
            <Button
              variant="primary"
              size="sm"
              disabled={!newRoom.trim() || busy === 'new'}
              onClick={() =>
                call(() => api.post('/api/v1/rooms', { label: newRoom.trim() }), 'new').then(() => setNewRoom(''))
              }
            >
              <Plus size={14} /> New room
            </Button>
          </div>
        }
      />
      <PageBody>
        {list.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No rooms yet"
            hint="A room holds a few agents, its own MCP connections, and a shared brief. They run in order, each seeing what the one before produced."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-[240px_1fr_260px]">
            {/* rooms */}
            <Card>
              <CardHeader title="Rooms" />
              <div className="px-2 pb-2">
                {list.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => setOpenId(r.id)}
                    className={
                      'flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-left text-[12.5px] ' +
                      (open?.id === r.id ? 'bg-surface-2 text-ink' : 'text-ink-2 hover:bg-surface-hover')
                    }
                  >
                    {live?.running && live.room_id === r.id ? (
                      <StatusDot state="online" />
                    ) : (
                      <StatusDot state="unknown" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{r.label}</span>
                    <span className="text-[11px] text-ink-3">{r.seats.length}</span>
                  </button>
                ))}
              </div>
            </Card>

            {/* the open room */}
            {open && (
              <Card>
                <CardHeader
                  title={open.label}
                  action={
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={live?.running || open.seats.length === 0 || busy === 'run'}
                        onClick={() =>
                          call(
                            () => api.post(`/api/v1/rooms/${open.id}/run`, { brief: open.brief }),
                            'run',
                            'Room started.',
                          )
                        }
                      >
                        <Play size={13} /> Run room
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (confirm(`Delete room “${open.label}”?`))
                            call(() => api.del(`/api/v1/rooms/${open.id}`), 'del').then(() => setOpenId(null))
                        }}
                      >
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  }
                />
                <div className="space-y-3 px-4 pb-4">
                  <Field label="Brief" hint="What the whole room is working on. Every agent sees this.">
                    {/* Uncontrolled + save on blur: the list polls every few
                        seconds, and a controlled value would fight the user's
                        cursor mid-sentence on each refetch. */}
                    <Textarea
                      key={`brief-${open.id}`}
                      rows={2}
                      defaultValue={open.brief}
                      placeholder="What is this room working on?"
                      onBlur={(e) => {
                        if (e.target.value !== open.brief)
                          call(() => api.post(`/api/v1/rooms/${open.id}`, { brief: e.target.value }), 'brief')
                      }}
                    />
                  </Field>

                  <Field
                    label="Room connections"
                    hint="Every agent in this room gets exactly these — that is what makes it a room."
                  >
                    <ConnectionPicker
                      connections={services}
                      selected={Object.fromEntries(services.map((s) => [s.id, open.mcp_services.includes(s.id)]))}
                      onChange={(next) =>
                        call(
                          () =>
                            api.post(`/api/v1/rooms/${open.id}`, {
                              mcp_services: Object.entries(next)
                                .filter(([, v]) => v)
                                .map(([k]) => k),
                            }),
                          'conns',
                        )
                      }
                    />
                  </Field>

                  {/* the seats — drop target */}
                  <div
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => {
                      if (dragging) dropIntoRoom(open, dragging, 'researcher')
                      setDragging(null)
                    }}
                    className="min-h-[110px] rounded-[var(--radius-md)] border border-dashed border-border-strong p-2"
                  >
                    {open.seats.length === 0 ? (
                      <p className="px-1 py-6 text-center text-[12px] text-ink-3">
                        Drag an agent here from the right. They run top to bottom, each seeing the work above it.
                      </p>
                    ) : (
                      <ol className="space-y-1.5">
                        {open.seats.map((s, i) => {
                          const isLive = live?.running && live.seat_id === s.id
                          return (
                            <li
                              key={s.id}
                              draggable
                              onDragStart={() => setDragSeat(s.id)}
                              onDragOver={(e) => e.preventDefault()}
                              onDrop={(e) => {
                                e.stopPropagation()
                                if (dragSeat) reorder(open, dragSeat, s.id)
                                else if (dragging) dropIntoRoom(open, dragging, s.role)
                                setDragSeat(null)
                                setDragging(null)
                              }}
                              className={
                                'flex items-start gap-2 rounded-[var(--radius-sm)] px-2 py-2 ' +
                                (isLive ? 'bg-accent/10 ring-1 ring-accent/40' : 'bg-surface-2')
                              }
                            >
                              <GripVertical size={13} className="mt-0.5 shrink-0 cursor-grab text-ink-3" />
                              <span className="mt-0.5 text-[11px] text-ink-3">{i + 1}</span>
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <Select
                                    className="h-6 max-w-[130px] text-[12px]"
                                    value={s.role}
                                    onChange={(e) =>
                                      call(
                                        () =>
                                          api.post(`/api/v1/rooms/${open.id}/seats/${s.id}`, {
                                            role: e.target.value,
                                          }),
                                        `seat-${s.id}`,
                                      )
                                    }
                                  >
                                    {(rooms.data?.roles ?? []).map((r) => (
                                      <option key={r} value={r}>
                                        {r}
                                      </option>
                                    ))}
                                  </Select>
                                  <span className="text-[11.5px] text-ink-3">{s.provider}</span>
                                  {isLive && <span className="text-[11px] text-accent">working…</span>}
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="ml-auto"
                                    onClick={() =>
                                      call(
                                        () => api.del(`/api/v1/rooms/${open.id}/seats/${s.id}`),
                                        `seat-${s.id}`,
                                      )
                                    }
                                  >
                                    <Trash2 size={12} />
                                  </Button>
                                </div>
                                <Input
                                  className="mt-1 h-6 text-[12px]"
                                  defaultValue={s.goal}
                                  placeholder={ROLE_HINT[s.role] ?? 'what this agent should do'}
                                  onBlur={(e) =>
                                    call(
                                      () =>
                                        api.post(`/api/v1/rooms/${open.id}/seats/${s.id}`, {
                                          goal: e.target.value,
                                        }),
                                      `seat-${s.id}`,
                                    )
                                  }
                                />
                              </div>
                            </li>
                          )
                        })}
                      </ol>
                    )}
                  </div>
                </div>
              </Card>
            )}

            {/* the bench */}
            <div className="space-y-4">
              <Card>
                <CardHeader title="Agents" subtitle="Drag one into a room" />
                <div className="px-2 pb-2">
                  {linked.length === 0 ? (
                    <div className="px-2 py-3">
                      <p className="text-[12px] text-ink-3">
                        No linked provider accounts yet.
                      </p>
                      <Button variant="default" size="sm" className="mt-2" onClick={() => navigate('settings')}>
                        Set one up
                      </Button>
                    </div>
                  ) : (
                    linked.map((a) => (
                      <div
                        key={`${a.provider}/${a.id}`}
                        draggable
                        onDragStart={() => setDragging(a)}
                        onDragEnd={() => setDragging(null)}
                        className="mb-1 cursor-grab rounded-[var(--radius-sm)] bg-surface-2 px-2.5 py-2"
                      >
                        <div className="text-[12.5px] text-ink">{a.label}</div>
                        <div className="text-[11px] text-ink-3">
                          {a.providerLabel}
                          {a.role ? ` · ${a.role}` : ''}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </Card>

              <Card>
                <CardHeader title="Recent room runs" />
                <div className="px-2 pb-2">
                  {(rooms.data?.runs ?? []).length === 0 ? (
                    <p className="px-2 py-3 text-[12px] text-ink-3">Nothing has run yet.</p>
                  ) : (
                    (rooms.data?.runs ?? []).map((run) => (
                      <div key={run.id} className="border-b border-border px-2 py-2 last:border-0">
                        <div className="flex items-center gap-2">
                          <StatusDot state={run.ok ? 'online' : 'offline'} />
                          <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{run.room_label}</span>
                          <span className="text-[11px] text-ink-3">${run.cost_usd}</span>
                        </div>
                        <div className="mt-0.5 text-[11px] text-ink-3">
                          {run.steps.map((s) => `${s.ok ? '✓' : '✗'} ${s.label}`).join('  ')}
                        </div>
                        {run.error && <div className="mt-0.5 text-[11px] text-danger">{run.error}</div>}
                      </div>
                    ))
                  )}
                </div>
              </Card>
            </div>
          </div>
        )}
      </PageBody>
    </>
  )
}
