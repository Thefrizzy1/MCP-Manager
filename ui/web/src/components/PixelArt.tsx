/** Pixel-art workers and machines for the Overview. Everything is plain SVG rects. */

const BASE = [
  '....DD........', '....DD........', '..BBBBBB......', '..BEBBEB......', '..BBBBBB......', '..BDDDDB......',
  '..BBBBBB......', '.BBBBBBBB.....', '.BBBDDBBB.....', '.BBBBBBBB.....', '..BB..BB......', '..DD..DD......',
]
/** What each worker holds: [row, column, pixels]. */
const PROPS: Record<string, [number, number, string][]> = {
  researcher: [[3, 2, 'DEDDED'], [6, 9, 'TTTT'], [7, 9, 'EEEE'], [8, 9, 'TTTT'], [9, 9, 'DDDD']], // glasses, books
  workflow_maker: [[5, 9, 'TTTTT'], [6, 9, 'TEEET'], [7, 9, 'TEEET'], [8, 9, 'TTTTT'], [9, 8, 'DDDDDD']], // laptop
  editor: [[5, 9, 'TETET'], [6, 9, 'ETETE'], [7, 9, 'TTTTT'], [8, 9, 'TTTTT']], // clapperboard
  manager: [[4, 10, 'DD'], [5, 9, 'TTTT'], [6, 9, 'TEET'], [7, 9, 'TTTT'], [8, 9, 'TEET'], [9, 9, 'TTTT']], // clipboard
  workflow_manager: [[4, 9, 'T.T'], [5, 9, 'TTT'], [6, 10, 'T'], [7, 10, 'T'], [8, 10, 'T'], [9, 10, 'D']], // wrench
  publisher: [[5, 11, 'TT'], [6, 9, 'TTTT'], [7, 9, 'TTTT'], [8, 11, 'TT'], [6, 13, 'E'], [7, 13, 'E']], // megaphone
}
/** body, dark, light tint per worker */
export const WORKER_RAMP: Record<string, [string, string, string]> = {
  researcher: ['#378ADD', '#0C447C', '#85B7EB'],
  workflow_maker: ['#7F77DD', '#3C3489', '#AFA9EC'],
  editor: ['#D85A30', '#712B13', '#F0997B'],
  manager: ['#BA7517', '#633806', '#EF9F27'],
  workflow_manager: ['#D4537E', '#72243E', '#ED93B1'],
  publisher: ['#1D9E75', '#085041', '#5DCAA5'],
}
const GRAY: [string, string, string] = ['#888780', '#444441', '#B4B2A9']

export function PixelRobot({ kind, x, y, px, active, busy }: { kind: string; x: number; y: number; px: number; active: boolean; busy: boolean }) {
  const c = active ? (WORKER_RAMP[kind] ?? GRAY) : GRAY
  const rows = BASE.map((r) => r.split(''))
  for (const [row, col, str] of PROPS[kind] ?? []) str.split('').forEach((ch, k) => ch !== '.' && (rows[row][col + k] = ch))
  return (
    <g shapeRendering="crispEdges" opacity={active ? 1 : 0.55} className={busy ? 'px-bob' : undefined}>
      {rows.flatMap((r, ry) =>
        r.map((ch, rx) =>
          ch === '.' ? null : (
            <rect key={`${rx}-${ry}`} x={x + rx * px} y={y + ry * px} width={px} height={px}
              fill={ch === 'B' ? c[0] : ch === 'D' ? c[1] : ch === 'T' ? c[2] : '#F1EFE8'} />
          ),
        ),
      )}
    </g>
  )
}

const SERVER = [
  'DDDDDDDDDDDDDDDD', 'DBBBBBBBBBBBBBBD', 'DBLBLBBBBBBBBBBD', 'DBBBBBBBBBBBBBBD', 'DBSSSSSBBBBBBBBD', 'DBBBBBBBBBBBBBBD',
  'DBSSSSSBBBBBBBBD', 'DBBBBBBBBBBBBBBD', 'DBSSSSSBBLBLBLBD', 'DBBBBBBBBBBBBBBD', 'DDDDDDDDDDDDDDDD', '.DD..........DD.',
]

/** A little rack unit. On: LEDs blink and the fans turn (faster while working).
 *  Off: grey, still, with dust on top and a cobweb in the corner. */
export function PixelServer({ x, y, on, busy, fans }: { x: number; y: number; on: boolean; busy: boolean; fans: number }) {
  const s = 6
  return (
    <g shapeRendering="crispEdges" opacity={on ? 1 : 0.6}>
      {SERVER.flatMap((row, ry) =>
        row.split('').map((ch, rx) => {
          if (ch === '.') return null
          const fill = ch === 'D' ? '#444441' : ch === 'S' ? '#5F5E5A' : ch === 'L' ? (on ? '#5DCAA5' : '#5F5E5A') : on ? '#888780' : '#B4B2A9'
          return (
            <rect key={`${rx}-${ry}`} x={x + rx * s} y={y + ry * s} width={s} height={s} fill={fill}
              className={ch === 'L' && on ? 'px-led on' : undefined} style={ch === 'L' && on ? { animationDelay: `${rx * 0.13}s` } : undefined} />
          )
        }),
      )}
      {Array.from({ length: fans }, (_, i) => {
        const fx = x + 78 - i * 26
        const fy = y + 30
        return (
          <g key={i} shapeRendering="auto">
            <circle cx={fx} cy={fy} r={11} fill="#444441" />
            <g className={`px-fan${on ? (busy ? ' busy' : ' on') : ''}`}>
              {[20, 110].map((a) => (
                <rect key={a} x={fx - 9} y={fy - 2} width={18} height={4} fill={on ? '#85B7EB' : '#888780'} transform={`rotate(${a} ${fx} ${fy})`} />
              ))}
            </g>
          </g>
        )
      })}
      {!on && (
        <g shapeRendering="auto">
          {[[6, -4], [30, -4], [52, -4], [70, -4], [18, 8], [60, 20], [84, 44]].map(([dx, dy]) => (
            <rect key={`${dx}-${dy}`} x={x + dx} y={y + dy} width={4} height={4} fill="#D3D1C7" />
          ))}
          <path
            d={`M${x} ${y} l-16 -4 M${x} ${y} l-4 -16 M${x} ${y} l-13 -13 M${x - 10} ${y - 2} q-1 -7 7 -9 M${x - 6} ${y - 1} q0 -4 4 -5`}
            fill="none" stroke="#B4B2A9" strokeWidth={1}
          />
        </g>
      )}
    </g>
  )
}

const LAPTOP_OPEN = [
  '..DDDDDDDDDDDD..', '..DSSSSSSSSSSD..', '..DSSSSSSSSSSD..', '..DSSSSSSSSSSD..', '..DSSSSSSSSSSD..', '..DSSSSSSSSSSD..',
  '..DSSSSSSSSSSD..', '..DDDDDDDDDDDD..', '.BBBBBBBBBBBBBB.', 'BBKKKKKKKKKKKKBB', 'BBBBBBTTTTBBBBBB', 'DDDDDDDDDDDDDDDD',
]
const LAPTOP_CLOSED = [
  '................', '................', '................', '................', '................', '................',
  '................', '................', '................', '.DDDDDDDDDDDDDD.', 'BBBBBBBBBBBBBBBB', 'DDDDDDDDDDDDDDDD',
]

/** A laptop: lid open with a lit screen when it is on, lid shut when it is off. */
export function PixelLaptop({ x, y, on, busy }: { x: number; y: number; on: boolean; busy: boolean }) {
  const s = 6
  const rows = on ? LAPTOP_OPEN : LAPTOP_CLOSED
  return (
    <g shapeRendering="crispEdges" opacity={on ? 1 : 0.6}>
      {rows.flatMap((row, ry) =>
        row.split('').map((ch, rx) => {
          if (ch === '.') return null
          const fill =
            ch === 'D' ? '#444441' : ch === 'S' ? (busy ? '#AFA9EC' : '#85B7EB') : ch === 'K' ? '#5F5E5A' : ch === 'T' ? '#B4B2A9' : on ? '#888780' : '#B4B2A9'
          return (
            <rect key={`${rx}-${ry}`} x={x + rx * s} y={y + ry * s} width={s} height={s} fill={fill}
              className={ch === 'S' && busy && (rx + ry) % 3 === 0 ? 'px-led on' : undefined} />
          )
        }),
      )}
      {on && (
        <g>
          {[[4, 2, 5], [4, 4, 7], [4, 6, 4]].map(([cx, cy, w]) => (
            <rect key={cy} x={x + cx * s} y={y + cy * s + 1} width={w * s} height={3} fill="#F1EFE8" opacity={0.85} />
          ))}
        </g>
      )}
    </g>
  )
}
