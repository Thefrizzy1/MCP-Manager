/**
 * The floor: rooms drawn as spaces on a plan, connected in the order work moves.
 *
 * A list of rooms told you what existed. It did not tell you the thing that
 * actually matters — who is in which room, which desk the work is at right now,
 * and what happens after this room finishes. So the floor is laid out as the
 * pipeline: a chain of rooms is a row of connected spaces, and the baton visibly
 * sits at one desk.
 *
 * Drawn as line work (hairlines, fills, no shadows) because that is what a plan
 * is, and it happens to be exactly the app's existing visual language — so this
 * reads as a room inside Plutus, not a toy bolted onto it.
 */
import { useRef } from 'react'
import { ArrowRight, Clock, Link2, Play, Unlink, UserPlus } from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/cn'

export interface FloorSeat {
  id: string
  role: string
  label: string
  provider: string
  account_id: string
  goal: string
}
export interface FloorRoom {
  id: string
  label: string
  brief: string
  mcp_services: string[]
  next_room?: string
  colour?: string
  seats: FloorSeat[]
}

/** Room tag -> the token that draws it. A lookup rather than an interpolated
 *  `var(--room-${colour})`, so a colour that is not in the palette falls back
 *  visibly instead of resolving to nothing and drawing a transparent rule. */
export const ROOM_COLOUR: Record<string, string> = {
  slate: 'var(--room-slate)',
  indigo: 'var(--room-indigo)',
  violet: 'var(--room-violet)',
  teal: 'var(--room-teal)',
  amber: 'var(--room-amber)',
  rose: 'var(--room-rose)',
  lime: 'var(--room-lime)',
}

export const roomColour = (name?: string) => ROOM_COLOUR[name || ''] ?? ROOM_COLOUR.slate
export interface FloorLive {
  room_id: string
  run_id: string
  seat_id: string
  running: boolean
}
export interface FloorSchedule {
  cron: string
  timezone: string
  enabled: boolean
  name: string
}
export interface FloorLastRun {
  ok: boolean
  started: string
  cost_usd: number
}

/** "0 3 * * *" → "03:00 nightly". Falls back to the raw expression, which is
 *  still more use than nothing for a cron a human wrote by hand. */
export function cronLabel(cron: string): string {
  const p = cron.trim().split(/\s+/)
  if (p.length !== 5) return cron
  const [min, hr, dom, mon, dow] = p
  const time = /^\d+$/.test(min) && /^\d+$/.test(hr)
    ? `${hr.padStart(2, '0')}:${min.padStart(2, '0')}`
    : null
  if (!time) return cron
  if (dom === '*' && mon === '*' && dow === '*') return `${time} nightly`
  if (dom === '*' && mon === '*' && /^[0-6]$/.test(dow)) {
    return `${time} ${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][Number(dow)]}`
  }
  return `${time} · ${dom} ${mon} ${dow}`
}

/** Role → the plate on the desk. Letters, not icons: five roles read faster as
 *  letters than as five glyphs a viewer has to learn.
 *
 *  Two letters, not one. Researcher and reviewer both start with R, so single
 *  initials forced one of them onto an unrelated letter ("V" for reviewer),
 *  which is a thing the reader has to be taught rather than can read. */
const ROLE_PLATE: Record<string, string> = {
  manager: 'Mg',
  researcher: 'Rs',
  developer: 'Dv',
  reviewer: 'Rv',
  writer: 'Wr',
}

/** Rooms grouped into the chains they form via next_room.
 *
 * A room reached by another room's next_room is not a chain head, so it is drawn
 * inside that chain rather than again on its own. Cycles and rooms pointing at a
 * deleted id are handled by seen-tracking — the backend guards those at run
 * time, but the floor must not hang on data that is briefly inconsistent.
 */
export function chainsOf(rooms: FloorRoom[]): FloorRoom[][] {
  const byId = new Map(rooms.map((r) => [r.id, r]))
  const isFollower = new Set(
    rooms.map((r) => r.next_room).filter((id): id is string => Boolean(id) && byId.has(id!)),
  )
  const chains: FloorRoom[][] = []
  const placed = new Set<string>()

  const walk = (head: FloorRoom) => {
    const chain: FloorRoom[] = []
    let cur: FloorRoom | undefined = head
    while (cur && !placed.has(cur.id)) {
      placed.add(cur.id)
      chain.push(cur)
      cur = cur.next_room ? byId.get(cur.next_room) : undefined
    }
    if (chain.length) chains.push(chain)
  }

  rooms.filter((r) => !isFollower.has(r.id)).forEach(walk)
  // Anything left is part of a cycle — draw it rather than dropping it silently.
  rooms.filter((r) => !placed.has(r.id)).forEach(walk)
  return chains
}

function Desk({
  seat,
  index,
  total,
  live,
  onDragStart,
  onDragEnd,
  onDrop,
  onOpen,
  onMove,
}: {
  seat: FloorSeat
  index: number
  total: number
  live: boolean
  onDragStart: () => void
  onDragEnd: () => void
  onDrop: (e: React.DragEvent) => void
  onOpen: () => void
  onMove: (delta: -1 | 1) => void
}) {
  const position = `${index + 1} of ${total}`
  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
      onClick={onOpen}
      onKeyDown={(e) => {
        // Order is the whole handoff model, and it was reachable by mouse only.
        // Alt+Arrow moves a seat without a pointer; plain arrows still scroll.
        if (!e.altKey) return
        if (e.key === 'ArrowUp' && index > 0) {
          e.preventDefault()
          onMove(-1)
        } else if (e.key === 'ArrowDown' && index < total - 1) {
          e.preventDefault()
          onMove(1)
        }
      }}
      title={`${seat.label || seat.role} — ${seat.goal || 'no specific goal set'}\nRuns ${position}. Alt+↑/↓ to move.`}
      aria-label={`${seat.label || seat.role}, ${seat.role}, runs ${position}${
        live ? ', working now' : ''
      }`}
      className={cn(
        'group relative flex w-full items-center gap-2 rounded-[var(--radius-sm)] border px-2 py-1.5 text-left',
        'cursor-grab active:cursor-grabbing',
        live
          ? 'floor-desk-live border-accent bg-accent-weak'
          : 'border-border bg-surface hover:border-border-strong',
      )}
    >
      <span
        className={cn(
          'grid h-6 w-7 shrink-0 place-items-center rounded-[var(--radius-sm)] font-mono text-[10.5px] font-semibold',
          live ? 'bg-accent text-white' : 'bg-surface-2 text-ink-2',
        )}
        aria-hidden
      >
        {ROLE_PLATE[seat.role] ?? seat.role.slice(0, 2)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] leading-tight text-ink">
          {seat.label || seat.role}
        </span>
        <span className="block truncate text-[11px] leading-tight text-ink-3">
          {live ? 'working now' : seat.provider}
        </span>
      </span>
      <span className="shrink-0 font-mono text-[10.5px] text-ink-3" aria-hidden>
        {index + 1}
      </span>
    </button>
  )
}

function Corridor({ active, onUnlink }: { active: boolean; onUnlink: () => void }) {
  return (
    <button
      type="button"
      onClick={onUnlink}
      className="group relative flex shrink-0 items-center self-center px-1"
      title="Work moves this way — the next room starts on the same folder, told what this one produced.\nClick to unlink."
      aria-label="then — click to unlink"
    >
      <span className="h-px w-4 bg-border-strong" aria-hidden />
      <ArrowRight
        size={13}
        className={cn(
          'shrink-0 group-hover:hidden',
          active ? 'floor-baton text-accent' : 'text-ink-3',
        )}
        aria-hidden
      />
      <Unlink size={13} className="hidden shrink-0 text-danger group-hover:block" aria-hidden />
      <span className="h-px w-4 bg-border-strong" aria-hidden />
    </button>
  )
}

/** The out-handle: drag it onto another room to make that room run next. */
function ChainHandle({ roomId, onGrab }: { roomId: string; onGrab: (id: string | null) => void }) {
  return (
    <span
      draggable
      onDragStart={(e) => {
        e.stopPropagation()
        onGrab(roomId)
      }}
      onDragEnd={() => onGrab(null)}
      title="Drag onto another room to run it after this one"
      aria-hidden
      className="ml-auto grid h-5 w-5 cursor-crosshair place-items-center rounded-[var(--radius-sm)] text-ink-3 hover:bg-surface-2 hover:text-accent"
    >
      <Link2 size={12} />
    </span>
  )
}

export function Floor({
  rooms,
  live,
  selectedId,
  busy,
  draggingAgent,
  scheduled,
  lastRuns,
  onSelect,
  onRun,
  onDropAgent,
  onReorder,
  onChain,
}: {
  rooms: FloorRoom[]
  live?: FloorLive
  selectedId: string | null
  busy: string
  draggingAgent: boolean
  scheduled: Record<string, FloorSchedule>
  lastRuns: Record<string, FloorLastRun>
  /** Set (or with '' clear) which room runs after ``fromId``. */
  onChain: (fromId: string, toId: string) => void
  onSelect: (roomId: string) => void
  onRun: (room: FloorRoom) => void
  /** ``role`` is the role of the desk the agent was dropped on, so dropping
   *  someone onto the Engineer desk hires a developer. Dropping on open floor
   *  passes nothing and the caller picks a default. */
  onDropAgent: (room: FloorRoom, role?: string) => void
  onReorder: (room: FloorRoom, fromSeatId: string, toSeatId: string) => void
}) {
  const chains = chainsOf(rooms)
  // A ref, not state or a local: the rooms query polls every few seconds, so a
  // refetch lands mid-drag and would wipe a plain local — the seat you were
  // moving would silently become a no-op on drop.
  const dragSeat = useRef<string | null>(null)
  // Chaining used to live in a dropdown three fields down a side panel, which is
  // a strange place for the one relationship the floor exists to show. Drag a
  // room's out-handle onto another room to wire them.
  const chainFrom = useRef<string | null>(null)

  // A chain has to stay on one line to read as a pipeline, but a floor of
  // twenty unchained rooms as twenty one-room rows is a list again with extra
  // steps. Singles flow and wrap; chains get their own line.
  const singles = chains.filter((c) => c.length === 1)
  const pipelines = chains.filter((c) => c.length > 1)

  return (
    <div className="floor-canvas overflow-x-auto rounded-[var(--radius)] border border-border p-4">
      <div className="flex min-w-fit flex-col gap-4">
        {[...pipelines, ...(singles.length ? [singles.flat()] : [])].map((chain, ci) => (
          <div
            key={chain[0].id}
            className={cn(
              'flex items-stretch',
              // the singles row is the only one allowed to wrap
              ci === pipelines.length && 'flex-wrap gap-3',
            )}
          >
            {chain.map((room, i) => {
              const isLive = Boolean(live?.running && live.room_id === room.id)
              const liveIndex = isLive ? room.seats.findIndex((s) => s.id === live?.seat_id) : -1
              const selected = selectedId === room.id
              const sched = scheduled[room.id]
              const last = lastRuns[room.id]
              const nextLive = Boolean(
                live?.running && chain[i + 1] && live.room_id === chain[i + 1].id,
              )
              return (
                <div key={room.id} className="flex items-stretch">
                  <section
                    onDragOver={(e) => {
                      if (draggingAgent || chainFrom.current) e.preventDefault()
                    }}
                    onDrop={() => {
                      const from = chainFrom.current
                      chainFrom.current = null
                      // A chain drop wins: you were explicitly dragging a
                      // corridor, not an agent.
                      if (from) {
                        if (from !== room.id) onChain(from, room.id)
                        return
                      }
                      if (draggingAgent) onDropAgent(room)
                    }}
                    aria-current={selected ? 'true' : undefined}
                    className={cn(
                      'flex w-[228px] flex-col rounded-[var(--radius)] border bg-surface',
                      selected ? 'border-accent' : 'border-border-strong',
                      draggingAgent && 'border-dashed border-accent',
                    )}
                  >
                    {/* The room's tag. A hairline across the top of the card,
                        not a fill: it has to identify the room at a glance
                        without competing with the accent, which is the only
                        colour on the floor that means "happening now". */}
                    <span
                      className="h-[3px] shrink-0 rounded-t-[var(--radius)]"
                      style={{ backgroundColor: roomColour(room.colour) }}
                      aria-hidden
                    />

                    {/* door plate */}
                    <header className="flex items-center gap-1.5 border-b border-border px-2.5 py-2">
                      <span
                        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', isLive && 'bg-ok')}
                        style={isLive ? undefined : { backgroundColor: roomColour(room.colour) }}
                        aria-hidden
                      />
                      <button
                        type="button"
                        onClick={() => onSelect(room.id)}
                        className="min-w-0 flex-1 truncate text-left text-[13px] font-semibold text-ink hover:text-accent"
                      >
                        {room.label}
                      </button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-1.5"
                        title={
                          room.seats.length === 0
                            ? 'This room is empty — put someone in it first'
                            : live?.running
                              ? 'A room is already running'
                              : `Run ${room.label}`
                        }
                        aria-label={`Run ${room.label}`}
                        disabled={
                          room.seats.length === 0 || Boolean(live?.running) || busy === 'run'
                        }
                        onClick={() => onRun(room)}
                      >
                        <Play size={12} />
                      </Button>
                      <ChainHandle roomId={room.id} onGrab={(id) => (chainFrom.current = id)} />
                    </header>

                    {/* desks */}
                    <div className="flex flex-1 flex-col gap-1.5 p-2">
                      {room.seats.length === 0 ? (
                        <div className="flex flex-1 flex-col items-center justify-center gap-1.5 rounded-[var(--radius-sm)] border border-dashed border-border-strong px-2 py-5 text-center">
                          <UserPlus size={15} className="text-ink-3" aria-hidden />
                          <span className="text-[11.5px] leading-snug text-ink-3">
                            Empty room.
                            <br />
                            Drag someone in.
                          </span>
                        </div>
                      ) : (
                        room.seats.map((seat, si) => (
                          <Desk
                            key={seat.id}
                            seat={seat}
                            index={si}
                            total={room.seats.length}
                            live={Boolean(isLive && live?.seat_id === seat.id)}
                            onDragStart={() => {
                              dragSeat.current = seat.id
                            }}
                            // Without this, releasing a desk over open floor left
                            // the id set, and the next agent dragged in from the
                            // bench was misread as that reorder — the agent was
                            // never hired and an unrelated seat moved instead.
                            onDragEnd={() => {
                              dragSeat.current = null
                            }}
                            onDrop={(e) => {
                              e.stopPropagation()
                              const from = dragSeat.current
                              dragSeat.current = null
                              if (from) {
                                if (from !== seat.id) onReorder(room, from, seat.id)
                              } else if (draggingAgent) {
                                // Adopt the desk's role: dropping someone onto the
                                // Engineer desk should hire a developer, not
                                // silently a researcher.
                                onDropAgent(room, seat.role)
                              }
                            }}
                            onOpen={() => onSelect(room.id)}
                            onMove={(delta) => {
                              const to = room.seats[si + delta]
                              if (to) onReorder(room, seat.id, to.id)
                            }}
                          />
                        ))
                      )}
                    </div>

                    {/* how this room stands: scheduled? how did it go last time? */}
                    {(sched || last) && (
                      <div className="flex items-center gap-2 border-t border-border px-2.5 py-1 text-[11px]">
                        {sched && (
                          <span
                            className={cn(
                              'inline-flex items-center gap-1',
                              sched.enabled ? 'text-ink-2' : 'text-ink-3 line-through',
                            )}
                            title={
                              sched.enabled
                                ? `Runs on its own: ${sched.cron} ${sched.timezone}`
                                : `Schedule is paused: ${sched.cron} ${sched.timezone}`
                            }
                          >
                            <Clock size={11} aria-hidden />
                            {cronLabel(sched.cron)}
                          </span>
                        )}
                        {sched && last && <span aria-hidden className="text-ink-3">·</span>}
                        {last && (
                          <span
                            className={last.ok ? 'text-ink-3' : 'text-danger'}
                            title={`Last run ${last.ok ? 'succeeded' : 'failed'} — $${last.cost_usd}`}
                          >
                            last {last.ok ? 'ok' : 'failed'}
                          </span>
                        )}
                      </div>
                    )}

                    <footer className="flex items-center gap-2 border-t border-border px-2.5 py-1.5 text-[11px] text-ink-3">
                      {liveIndex >= 0 ? (
                        // During a run the seat count is the wrong thing to show —
                        // what you want to know is how far along it is.
                        <span className="font-medium text-accent">
                          seat {liveIndex + 1} of {room.seats.length}
                        </span>
                      ) : (
                        <span>
                          {room.seats.length === 0
                            ? 'no one yet'
                            : `${room.seats.length} ${room.seats.length === 1 ? 'seat' : 'seats'}`}
                        </span>
                      )}
                      <span aria-hidden>·</span>
                      <span className="truncate">
                        {room.mcp_services.length === 0
                          ? 'no connections'
                          : `${room.mcp_services.length} connections`}
                      </span>
                    </footer>
                  </section>

                  {/* Only a real pipeline gets corridors. The singles row is a
                      flattened collection of unrelated rooms — an arrow between
                      two of them would assert a handoff that does not exist. */}
                  {ci < pipelines.length && i < chain.length - 1 && (
                    <Corridor active={isLive || nextLive} onUnlink={() => onChain(room.id, '')} />
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
