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
import { ArrowRight, Play, UserPlus } from 'lucide-react'

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
  seats: FloorSeat[]
}
export interface FloorLive {
  room_id: string
  run_id: string
  seat_id: string
  running: boolean
}

/** Role → the single letter on the desk plate. Initials, not icons: five roles
 *  read faster as letters than as five glyphs a viewer has to learn. */
const ROLE_INITIAL: Record<string, string> = {
  manager: 'M',
  researcher: 'R',
  developer: 'D',
  reviewer: 'V',
  writer: 'W',
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
  live,
  onDragStart,
  onDrop,
  onOpen,
}: {
  seat: FloorSeat
  index: number
  live: boolean
  onDragStart: () => void
  onDrop: (e: React.DragEvent) => void
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
      onClick={onOpen}
      title={`${seat.label || seat.role} — ${seat.goal || 'no specific goal set'}`}
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
          'grid h-6 w-6 shrink-0 place-items-center rounded-[var(--radius-sm)] font-mono text-[11.5px] font-semibold',
          live ? 'bg-accent text-white' : 'bg-surface-2 text-ink-2',
        )}
        aria-hidden
      >
        {ROLE_INITIAL[seat.role] ?? seat.role.slice(0, 1).toUpperCase()}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] leading-tight text-ink">
          {seat.label || seat.role}
        </span>
        <span className="block truncate text-[11px] leading-tight text-ink-3">
          {live ? 'working now' : seat.provider}
        </span>
      </span>
      <span className="shrink-0 font-mono text-[10.5px] text-ink-3">{index + 1}</span>
    </button>
  )
}

function Corridor({ active }: { active: boolean }) {
  return (
    <div
      className="relative flex shrink-0 items-center self-center px-1"
      title="Work moves this way — the next room starts on the same folder, told what this one produced"
      aria-label="then"
    >
      <span className="h-px w-4 bg-border-strong" aria-hidden />
      <ArrowRight
        size={13}
        className={cn('shrink-0', active ? 'floor-baton text-accent' : 'text-ink-3')}
        aria-hidden
      />
      <span className="h-px w-4 bg-border-strong" aria-hidden />
    </div>
  )
}

export function Floor({
  rooms,
  live,
  selectedId,
  busy,
  draggingAgent,
  onSelect,
  onRun,
  onDropAgent,
  onReorder,
}: {
  rooms: FloorRoom[]
  live?: FloorLive
  selectedId: string | null
  busy: string
  draggingAgent: boolean
  onSelect: (roomId: string) => void
  onRun: (room: FloorRoom) => void
  onDropAgent: (room: FloorRoom, beforeSeatId?: string) => void
  onReorder: (room: FloorRoom, fromSeatId: string, toSeatId: string) => void
}) {
  const chains = chainsOf(rooms)
  // A ref, not state or a local: the rooms query polls every few seconds, so a
  // refetch lands mid-drag and would wipe a plain local — the seat you were
  // moving would silently become a no-op on drop.
  const dragSeat = useRef<string | null>(null)

  return (
    <div className="floor-canvas overflow-x-auto rounded-[var(--radius)] border border-border p-4">
      <div className="flex min-w-fit flex-col gap-4">
        {chains.map((chain) => (
          <div key={chain[0].id} className="flex items-stretch">
            {chain.map((room, i) => {
              const isLive = Boolean(live?.running && live.room_id === room.id)
              const selected = selectedId === room.id
              const nextLive = Boolean(
                live?.running && chain[i + 1] && live.room_id === chain[i + 1].id,
              )
              return (
                <div key={room.id} className="flex items-stretch">
                  <section
                    onDragOver={(e) => {
                      if (draggingAgent) e.preventDefault()
                    }}
                    onDrop={() => draggingAgent && onDropAgent(room)}
                    aria-current={selected ? 'true' : undefined}
                    className={cn(
                      'flex w-[228px] flex-col rounded-[var(--radius)] border bg-surface',
                      selected ? 'border-accent' : 'border-border-strong',
                      draggingAgent && 'border-dashed border-accent',
                    )}
                  >
                    {/* door plate */}
                    <header className="flex items-center gap-1.5 border-b border-border px-2.5 py-2">
                      <span
                        className={cn(
                          'h-1.5 w-1.5 shrink-0 rounded-full',
                          isLive ? 'bg-ok' : 'bg-border-strong',
                        )}
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
                            live={Boolean(isLive && live?.seat_id === seat.id)}
                            onDragStart={() => {
                              dragSeat.current = seat.id
                            }}
                            onDrop={(e) => {
                              e.stopPropagation()
                              const from = dragSeat.current
                              if (from && from !== seat.id) onReorder(room, from, seat.id)
                              else if (draggingAgent) onDropAgent(room, seat.id)
                              dragSeat.current = null
                            }}
                            onOpen={() => onSelect(room.id)}
                          />
                        ))
                      )}
                    </div>

                    <footer className="flex items-center gap-2 border-t border-border px-2.5 py-1.5 text-[11px] text-ink-3">
                      <span>
                        {room.seats.length === 0
                          ? 'no one yet'
                          : `${room.seats.length} ${room.seats.length === 1 ? 'seat' : 'seats'}`}
                      </span>
                      <span aria-hidden>·</span>
                      <span className="truncate">
                        {room.mcp_services.length === 0
                          ? 'no connections'
                          : `${room.mcp_services.length} connections`}
                      </span>
                    </footer>
                  </section>

                  {i < chain.length - 1 && <Corridor active={isLive || nextLive} />}
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
