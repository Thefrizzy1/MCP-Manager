import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Play, Trash2, GripVertical, Users, ArrowRight } from 'lucide-react'
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
import { ModelPicker } from '@/components/agents/ModelPicker'
import { linkedAccounts as toLinked, useProviders } from '@/lib/providers'
import { Floor, roomColour } from '@/components/rooms/Floor'
import { RunDetail } from '@/components/rooms/RunDetail'

interface Seat {
  id: string
  role: string
  label: string
  provider: string
  account_id: string
  goal: string
  model?: string
}
interface Room {
  id: string
  label: string
  brief: string
  mcp_services: string[]
  next_room?: string
  colour?: string
  seats: Seat[]
}
interface RoomPreset {
  id: string
  label: string
  description: string
  rooms: { label: string; colour: string; seats: number; roles: string[] }[]
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
  room_id: string
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
  /** room id -> its schedule, when the room runs on its own. */
  scheduled?: Record<string, { cron: string; timezone: string; enabled: boolean; name: string }>
  presets?: RoomPreset[]
  colours?: string[]
}
type LinkedAccount = ReturnType<typeof toLinked>[number]

const ROLE_HINT: Record<string, string> = {
  manager: 'reviews the work handed to it and directs the next person',
  researcher: 'gathers information and reports findings',
  developer: 'turns accepted findings into working changes',
  reviewer: 'checks the work against the brief',
  writer: 'produces the finished written output',
}

/** Start from a working pipeline instead of an empty room.
 *
 * The hard part of a room is not creating it — it is the brief, the seat order,
 * and a connection list narrow enough that the tool manifest does not drown the
 * task. A template ships all three, already chained, so the first run produces
 * something. */
function Templates({
  presets,
  accounts,
  busy,
  onInstall,
}: {
  presets: RoomPreset[]
  accounts: LinkedAccount[]
  busy: string
  onInstall: (presetId: string, acct: LinkedAccount) => void
}) {
  const [who, setWho] = useState('')
  if (presets.length === 0) return null

  const chosen = accounts.find((a) => `${a.provider}/${a.id}` === who) ?? accounts[0]

  return (
    <Card>
      <CardHeader
        title="Start from a template"
        subtitle="A whole pipeline — rooms, briefs and the chain between them"
      />
      <div className="space-y-2 px-4 pb-4">
        {accounts.length === 0 ? (
          <p className="text-[12px] text-ink-3">
            Link a provider account first — a template staffs every seat with one.
          </p>
        ) : (
          <>
            {accounts.length > 1 && (
              <Field label="Staff the seats with" hint="You can re-point any seat afterwards.">
                <Select value={who || `${chosen.provider}/${chosen.id}`} onChange={(e) => setWho(e.target.value)}>
                  {accounts.map((a) => (
                    <option key={`${a.provider}/${a.id}`} value={`${a.provider}/${a.id}`}>
                      {a.label} · {a.providerLabel}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            {presets.map((p) => (
              <div key={p.id} className="rounded-[var(--radius-sm)] border border-border p-2.5">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium text-ink">{p.label}</div>
                    <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{p.description}</p>
                  </div>
                  <Button
                    size="sm"
                    disabled={busy === `preset-${p.id}`}
                    onClick={() => onInstall(p.id, chosen)}
                  >
                    <Plus size={13} /> Add
                  </Button>
                </div>
                {/* What you are about to get, in the order it runs. */}
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {p.rooms.map((r, i) => (
                    <span key={r.label} className="flex items-center gap-1">
                      {i > 0 && <ArrowRight size={11} className="text-ink-3" aria-hidden />}
                      <span className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-2">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ backgroundColor: roomColour(r.colour) }}
                          aria-hidden
                        />
                        {r.label}
                        <span className="text-ink-3">{r.seats}</span>
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </Card>
  )
}

export function Rooms() {
  const toast = useToast()
  const rooms = useQuery({
    queryKey: ['rooms'],
    queryFn: () => api.get<RoomsResp>('/api/v1/rooms'),
    refetchInterval: 4000,
    // The floor is meant to be watchable — the live desk breathes so you can
    // leave a running room up on a second monitor. React Query stops interval
    // refetches while the document is hidden, so without this the floor freezes
    // the moment you look at another window and lies until you come back.
    refetchIntervalInBackground: true,
  })
  const conns = useQuery({
    queryKey: ['agent-conns'],
    queryFn: () => api.get<{ services?: Service[] }>('/api/v1/dashboard?sections=services'),
  })
  const providers = useProviders()

  const [newRoom, setNewRoom] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [dragging, setDragging] = useState<LinkedAccount | null>(null)
  const [dragSeat, setDragSeat] = useState<string | null>(null)
  const [busy, setBusy] = useState('')
  const [openRun, setOpenRun] = useState<string | null>(null)

  const linked = toLinked(providers.data?.providers)
  const accountLabel = (seat: Seat) =>
    linked.find((a) => a.provider === seat.provider && a.id === seat.account_id)?.label ||
    seat.account_id

  const list = rooms.data?.rooms ?? []
  const live = rooms.data?.live
  const open = list.find((r) => r.id === openId) ?? list[0] ?? null
  const services = (conns.data?.services ?? []).filter((s) => s.configured && !s.ignored)

  // Most recent run per room, keyed by id rather than label — a renamed room
  // would otherwise silently lose its own history. The list is newest-first, so
  // the first hit for a room wins and older ones are ignored.
  const lastRuns: Record<string, { ok: boolean; started: string; cost_usd: number }> = {}
  for (const run of rooms.data?.runs ?? []) {
    if (run.room_id && !lastRuns[run.room_id]) {
      lastRuns[run.room_id] = { ok: run.ok, started: run.started, cost_usd: run.cost_usd }
    }
  }

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

  async function installPreset(presetId: string, acct: LinkedAccount) {
    await call(
      () =>
        api.post(`/api/v1/rooms/presets/${presetId}`, {
          provider: acct.provider,
          account_id: acct.id,
        }),
      `preset-${presetId}`,
      'Rooms created and chained.',
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
          <div className="space-y-4">
            <EmptyState
              icon={Users}
              title="No rooms yet"
              hint="A room holds a few agents, its own MCP connections, and a shared brief. They run in order, each seeing what the one before produced."
            />
            <div className="mx-auto max-w-[460px]">
              <Templates
                presets={rooms.data?.presets ?? []}
                accounts={linked}
                busy={busy}
                onInstall={installPreset}
              />
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {/* The floor. Rooms are spaces, chains are corridors, and the seat
                that is working right now is lit. Clicking a room opens it below. */}
            <Floor
              rooms={list}
              live={live}
              selectedId={open?.id ?? null}
              busy={busy}
              draggingAgent={Boolean(dragging)}
              onSelect={setOpenId}
              onRun={(room) =>
                call(
                  () => api.post(`/api/v1/rooms/${room.id}/run`, { brief: room.brief }),
                  'run',
                  `${room.label} started.`,
                )
              }
              onDropAgent={(room, role) => {
                // Dropped on a desk? take that desk's role — hiring onto the
                // Engineer desk should give you a developer. Open floor defaults.
                if (dragging) dropIntoRoom(room, dragging, role || 'researcher')
                setDragging(null)
              }}
              onReorder={(room, fromId, toId) => reorder(room, fromId, toId)}
              scheduled={rooms.data?.scheduled ?? {}}
              lastRuns={lastRuns}
              onChain={(fromId, toId) =>
                call(
                  () => api.post(`/api/v1/rooms/${fromId}`, { next_room: toId }),
                  'chain',
                  toId ? 'Rooms linked.' : 'Link removed.',
                )
              }
            />

          <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
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

                  <Field label="Tag" hint="Shows on the floor, so a dozen rooms stay readable.">
                    <div className="flex flex-wrap gap-1.5">
                      {(rooms.data?.colours ?? []).map((c) => {
                        const on = (open.colour || 'slate') === c
                        return (
                          <button
                            key={c}
                            type="button"
                            title={c}
                            aria-label={c}
                            aria-pressed={on}
                            onClick={() =>
                              call(() => api.post(`/api/v1/rooms/${open.id}`, { colour: c }), 'colour')
                            }
                            className={
                              'h-5 w-5 rounded-full ring-offset-2 ring-offset-surface ' +
                              (on ? 'ring-2 ring-accent' : 'ring-1 ring-border-strong hover:ring-ink-3')
                            }
                            style={{ backgroundColor: roomColour(c) }}
                          />
                        )
                      })}
                    </div>
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

                  <Field
                    label="Then run"
                    hint="When this room finishes, the next one starts on the same working folder and is told what this room produced. That is how research reaches the developers."
                  >
                    <Select
                      value={open.next_room || ''}
                      onChange={(e) =>
                        call(() => api.post(`/api/v1/rooms/${open.id}`, { next_room: e.target.value }), 'next')
                      }
                    >
                      <option value="">Nothing — stop after this room</option>
                      {(rooms.data?.rooms || [])
                        .filter((r) => r.id !== open.id)
                        .map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.label}
                          </option>
                        ))}
                    </Select>
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
                                  <span className="text-[11.5px] text-ink-3">
                                    {s.provider} · {accountLabel(s)}
                                  </span>
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
                                {/* Per seat, because a room mixes providers and a
                                    model id only means something to one of them. */}
                                <div className="mt-1 max-w-[240px]">
                                  <ModelPicker
                                    provider={s.provider}
                                    accountId={s.account_id}
                                    value={s.model ?? ''}
                                    onChange={(m) =>
                                      call(
                                        () =>
                                          api.post(`/api/v1/rooms/${open.id}/seats/${s.id}`, { model: m }),
                                        `seat-${s.id}`,
                                      )
                                    }
                                    className="h-6 text-[12px]"
                                  />
                                </div>
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

              <Templates
                presets={rooms.data?.presets ?? []}
                accounts={linked}
                busy={busy}
                onInstall={installPreset}
              />

              <Card>
                <CardHeader title="Recent room runs" />
                <div className="px-2 pb-2">
                  {(rooms.data?.runs ?? []).length === 0 ? (
                    <p className="px-2 py-3 text-[12px] text-ink-3">Nothing has run yet.</p>
                  ) : (
                    (rooms.data?.runs ?? []).map((run) => (
                      <button
                        key={run.id}
                        type="button"
                        onClick={() => setOpenRun(run.id)}
                        title="See what each seat produced"
                        className="block w-full border-b border-border px-2 py-2 text-left last:border-0 hover:bg-surface-hover"
                      >
                        <div className="flex items-center gap-2">
                          <StatusDot state={run.ok ? 'online' : 'offline'} />
                          <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">{run.room_label}</span>
                          <span className="text-[11px] text-ink-3">${run.cost_usd}</span>
                        </div>
                        <div className="mt-0.5 text-[11px] text-ink-3">
                          {run.steps.map((s) => `${s.ok ? '✓' : '✗'} ${s.label}`).join('  ')}
                        </div>
                        {run.error && <div className="mt-0.5 text-[11px] text-danger">{run.error}</div>}
                      </button>
                    ))
                  )}
                </div>
              </Card>
            </div>
            </div>
          </div>
        )}
      </PageBody>
      <RunDetail runId={openRun} onClose={() => setOpenRun(null)} />
    </>
  )
}
