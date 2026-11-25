import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { ACTS, bluetoothReady, forgetKeys, keyName, learnKey, loadKeys, pairBluetooth, type Act } from '@/lib/remote'

interface Script {
  source: string
  title: string
  sections: { title: string; text: string }[]
}
interface Command {
  seq: number
  cmd: string
  source: string
}
interface Prefs {
  size: number
  speed: number // lines per minute
  mirror: boolean
  flip: boolean
  countdown: boolean
  wide: boolean
}
const PREFS: Prefs = { size: 84, speed: 22, mirror: false, flip: false, countdown: true, wide: true }

function loadPrefs(): Prefs {
  try {
    return { ...PREFS, ...JSON.parse(localStorage.getItem('prompter-prefs') ?? '{}') }
  } catch {
    return PREFS
  }
}
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const LINE = 1.35
/** The reading line sits this far down the screen. */
const MARK = 0.33

/** A few seconds of silence: while it plays, Android hands a remote's media buttons to this page. */
function silence(): string {
  const n = 8000 * 6
  const b = new Uint8Array(44 + n).fill(128)
  const v = new DataView(b.buffer)
  const put = (o: number, s: string) => [...s].forEach((c, i) => (b[o + i] = c.charCodeAt(0)))
  put(0, 'RIFF'); v.setUint32(4, 36 + n, true); put(8, 'WAVEfmt '); v.setUint32(16, 16, true)
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 8000, true); v.setUint32(28, 8000, true)
  v.setUint16(32, 1, true); v.setUint16(34, 8, true); put(36, 'data'); v.setUint32(40, n, true)
  return URL.createObjectURL(new Blob([b], { type: 'audio/wav' }))
}

function sourceFromHash(): string {
  return new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('source') ?? ''
}

/** Fullscreen teleprompter. Stays open on the tablet; scripts and commands arrive from anywhere. */
export function Prompter() {
  const [script, setScript] = useState<Script | null>(null)
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(false)
  const [count, setCount] = useState(0)
  const [prefs, setPrefs] = useState(loadPrefs)
  const [bar, setBar] = useState(true)
  const [remote, setRemote] = useState(false)
  const [started, setStarted] = useState(false)
  const [pct, setPct] = useState(0)
  const [offline, setOffline] = useState(false)

  const box = useRef<HTMLDivElement>(null)
  const parts = useRef<(HTMLElement | null)[]>([])
  const pos = useRef(0)
  const ratio = useRef(0)
  const live = useRef({ playing, prefs, script, count })
  live.current = { playing, prefs, script, count }

  const setPref = useCallback((change: Partial<Prefs>) => {
    setPrefs((p) => {
      const next = { ...p, ...change }
      try {
        localStorage.setItem('prompter-prefs', JSON.stringify(next))
      } catch {
        /* private window */
      }
      return next
    })
  }, [])

  const jump = useCallback((to: number) => {
    const el = box.current
    if (!el) return
    const max = el.scrollHeight - el.clientHeight
    pos.current = clamp(to, 0, Math.max(0, max))
    el.scrollTop = pos.current
    ratio.current = max > 0 ? pos.current / max : 0
    setPct(Math.round(ratio.current * 100))
  }, [])

  const load = useCallback(async (source: string) => {
    if (!source) return
    try {
      const s = await api.get<Script>(`/api/v1/prompter/script?source=${encodeURIComponent(source)}`)
      setPlaying(false)
      setCount(0)
      setScript(s)
      setError('')
      requestAnimationFrame(() => jump(0))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [jump])

  /** Where each part starts, as a scroll position that puts it on the reading line. */
  const starts = useCallback(() => {
    const el = box.current
    return el ? parts.current.filter(Boolean).map((p) => Math.max(0, p!.offsetTop - el.clientHeight * MARK)) : []
  }, [])

  const act = useCallback((a: string) => {
    const { playing, count, prefs } = live.current
    if (a === 'toggle' || a === 'play' || a === 'pause') {
      const going = playing || count > 0
      const want = a === 'toggle' ? !going : a === 'play'
      if (want && !going) {
        const el = box.current
        // Play after the end is a replay.
        if (el && el.scrollTop >= el.scrollHeight - el.clientHeight - 2) jump(0)
        if (prefs.countdown) setCount(3)
        else setPlaying(true)
      } else if (!want) {
        setCount(0)
        setPlaying(false)
      }
      if (want) setBar(false)
    } else if (a === 'restart') {
      jump(0)
    } else if (a === 'next') {
      const to = starts().find((s) => s > pos.current + 12)
      if (to !== undefined) jump(to)
    } else if (a === 'back') {
      // The start of this part; pressed again at the start, the part before it.
      const el = box.current
      const slack = el ? Math.min(120, el.clientHeight * 0.15) : 100
      jump([...starts()].reverse().find((s) => s < pos.current - slack) ?? 0)
    } else if (a === 'faster') setPref({ speed: clamp(prefs.speed + 2, 4, 90) })
    else if (a === 'slower') setPref({ speed: clamp(prefs.speed - 2, 4, 90) })
    else if (a === 'bigger') setPref({ size: clamp(prefs.size + 8, 32, 240) })
    else if (a === 'smaller') setPref({ size: clamp(prefs.size - 8, 32, 240) })
    else if (a === 'mirror') setPref({ mirror: !prefs.mirror })
    else if (a === 'flip') setPref({ flip: !prefs.flip })
  }, [jump, setPref, starts])

  // Countdown.
  useEffect(() => {
    if (!count) return
    const t = setTimeout(() => {
      if (count === 1) setPlaying(true)
      setCount(count - 1)
    }, 800)
    return () => clearTimeout(t)
  }, [count])

  // Scrolling.
  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    let shown = 0
    let frame = 0
    const step = (now: number) => {
      const el = box.current
      if (el) {
        const { size, speed } = live.current.prefs
        const dt = Math.min(0.1, (now - last) / 1000) // after a stall, carry on instead of leaping
        pos.current += ((size * LINE * speed) / 60) * dt
        el.scrollTop = pos.current
        const max = el.scrollHeight - el.clientHeight
        ratio.current = max > 0 ? clamp(pos.current / max, 0, 1) : 0
        if (now - shown > 300) {
          shown = now
          setPct(Math.round(ratio.current * 100))
        }
        if (pos.current >= max) {
          pos.current = max
          setPct(100)
          setPlaying(false)
          setBar(true)
          return
        }
      }
      last = now
      frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [playing])

  // Bigger text or a turned tablet keeps the place in the script.
  useLayoutEffect(() => {
    const keep = () => {
      const el = box.current
      if (!el) return
      pos.current = ratio.current * Math.max(0, el.scrollHeight - el.clientHeight)
      el.scrollTop = pos.current
    }
    keep()
    window.addEventListener('resize', keep)
    return () => window.removeEventListener('resize', keep)
  }, [prefs.size, prefs.wide])

  // First script: from the address, else whatever was last sent to the screen.
  useEffect(() => {
    const open = async () => {
      const s = sourceFromHash()
      if (s) return load(s)
      const r = await api.get<{ current: string }>('/api/v1/prompter').catch(() => null)
      if (r?.current) await load(r.current)
    }
    void open()
    const onHash = () => void load(sourceFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [load])

  // Commands from another device.
  useEffect(() => {
    let stop = false
    let after = -1
    const run = async () => {
      while (!stop) {
        try {
          const r = await api.get<{ seq: number; commands: Command[] }>(`/api/v1/prompter/commands?after=${after}`)
          if (stop) return
          after = r.seq
          setOffline(false)
          for (const c of r.commands) {
            if (c.cmd === 'load') await load(c.source)
            else act(c.cmd)
          }
        } catch {
          setOffline(true)
          await new Promise((ok) => setTimeout(ok, 2000))
        }
      }
    }
    void run()
    return () => {
      stop = true
    }
  }, [act, load])

  // Tell the controls where the screen is.
  const part = (() => {
    if (!script) return ''
    let i = 0
    starts().forEach((s, n) => {
      if (s <= pos.current + 40) i = n
    })
    return script.sections[i]?.title ?? ''
  })()
  const status = useRef({})
  status.current = { source: script?.source ?? '', title: script?.title ?? '', playing: playing || count > 0, percent: pct, part, speed: prefs.speed, size: prefs.size }
  useEffect(() => {
    const send = () => void api.post('/api/v1/prompter/status', status.current).catch(() => {})
    send()
    const t = setInterval(send, 4000)
    return () => clearInterval(t)
  }, [playing, script, prefs.speed, prefs.size])

  // Buttons: keyboard-style remotes and Bluetooth ones end up here.
  const learning = useRef<((key: string) => void) | null>(null)
  const lastPress = useRef({ a: '', t: 0 })
  const press = useCallback((key: string) => {
    if (learning.current) {
      learning.current(key)
      return true
    }
    const a = loadKeys()[key]
    // Some remotes send one press twice (as a key and as a media button): that must not play and pause.
    const now = performance.now()
    if (a && !(a === lastPress.current.a && now - lastPress.current.t < 250)) {
      lastPress.current = { a, t: now }
      act(a)
    }
    return !!a
  }, [act])
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'Escape' || e.key === 'Tab') return
      if (e.repeat) {
        e.preventDefault()
        return
      }
      if (press(keyName(e))) e.preventDefault()
    }
    window.addEventListener('keydown', down, true)
    return () => window.removeEventListener('keydown', down, true)
  }, [press])

  // Keep the screen on, and take media buttons.
  const audio = useRef<HTMLAudioElement | null>(null)
  const start = useCallback(async () => {
    setStarted(true)
    await document.documentElement.requestFullscreen?.().catch(() => {})
    if (!audio.current) {
      audio.current = new Audio(silence())
      audio.current.loop = true
    }
    await audio.current.play().catch(() => {})
    const ms = navigator.mediaSession
    if (ms) {
      const on = (a: MediaSessionAction, fn: () => void) => {
        try {
          ms.setActionHandler(a, fn)
        } catch {
          /* this browser does not offer that button */
        }
      }
      // The silence must keep playing, so pause only ever means the script.
      const again = () => void audio.current?.play().catch(() => {})
      on('play', () => { again(); press('MediaPlayPause') })
      on('pause', () => { again(); press('MediaPlayPause') })
      on('nexttrack', () => press('MediaTrackNext'))
      on('previoustrack', () => press('MediaTrackPrevious'))
    }
  }, [press])
  useEffect(() => () => audio.current?.pause(), [])
  useEffect(() => {
    let lock: { release(): Promise<void> } | null = null
    const hold = async () => {
      if (document.visibilityState !== 'visible' || !('wakeLock' in navigator)) return
      lock = await navigator.wakeLock.request('screen').catch(() => null)
    }
    void hold()
    document.addEventListener('visibilitychange', hold)
    return () => {
      document.removeEventListener('visibilitychange', hold)
      void lock?.release().catch(() => {})
    }
  }, [])

  const flipped = `scale(${prefs.mirror ? -1 : 1}, ${prefs.flip ? -1 : 1})`
  const btn = 'h-14 min-w-14 rounded-xl bg-white/12 px-4 text-[17px] font-medium text-white active:bg-white/30'

  return (
    <div className="fixed inset-0 z-50 select-none overflow-hidden bg-black text-white">
      <div className="absolute inset-0" style={{ transform: flipped }}>
        <div
          ref={box}
          className="prompter-scroll absolute inset-0 overflow-y-auto"
          onClick={() => {
            if (!started) void start()
            if (playing || count) act('pause')
            else setBar((b) => !b)
          }}
          onScroll={(e) => {
            // A finger moved it: carry on from there.
            const el = e.currentTarget
            if (Math.abs(el.scrollTop - pos.current) > 3) {
              pos.current = el.scrollTop
              const max = el.scrollHeight - el.clientHeight
              ratio.current = max > 0 ? pos.current / max : 0
              setPct(Math.round(ratio.current * 100))
            }
          }}
        >
          <div className="mx-auto px-[6vw]" style={{ maxWidth: prefs.wide ? undefined : 1000, paddingTop: `${MARK * 100}vh`, paddingBottom: '67vh' }}>
            {script?.sections.map((s, i) => (
              <section key={i} ref={(el) => { parts.current[i] = el }} style={{ fontSize: prefs.size, lineHeight: LINE }} className="font-semibold">
                {s.title && <div className="mb-[0.2em] text-sky-300" style={{ fontSize: '0.45em' }}>{s.title}</div>}
                {s.text.split('\n').map((line, n) =>
                  !line.trim() ? (
                    <div key={n} style={{ height: '0.6em' }} />
                  ) : /^\s*[[(].*[\])]\s*$/.test(line) ? (
                    <div key={n} className="text-amber-300" style={{ fontSize: '0.5em' }}>{line}</div>
                  ) : (
                    <div key={n} style={{ overflowWrap: 'break-word' }}>{line}</div>
                  ),
                )}
                <div style={{ height: '1em' }} />
              </section>
            ))}
            {script && <div className="text-white/40" style={{ fontSize: prefs.size * 0.5 }}>End. Play starts again from the top.</div>}
          </div>
        </div>
        {script && (
          <div className="pointer-events-none absolute left-0 text-red-500" style={{ top: `${MARK * 100}vh`, fontSize: prefs.size * 0.6, lineHeight: `${prefs.size * LINE}px` }}>▶</div>
        )}
        <div className="pointer-events-none absolute bottom-0 left-0 h-1.5 bg-sky-400" style={{ width: `${pct}%` }} />
        {count > 0 && <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/60 text-[40vh] font-bold">{count}</div>}
      </div>

      {!script && (
        <p className="absolute inset-0 flex items-center justify-center p-10 text-center text-[28px] text-white/70">
          {error || 'Waiting for a script. Open Film in Plutus and press “To the prompter”.'}
        </p>
      )}
      {script && error && <p className="absolute inset-x-0 top-0 bg-red-700 p-3 text-center text-[18px]">{error}</p>}
      {offline && <span className="absolute right-3 top-3 rounded-full bg-amber-600 px-3 py-1 text-[14px]">No connection. Buttons here still work.</span>}

      {bar && (
        <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-center justify-center gap-2 bg-black/85 p-3">
          {!started && <button className={btn + ' !bg-sky-600'} onClick={() => void start()}>Fullscreen</button>}
          <button className={btn} onClick={() => act('restart')} aria-label="Replay from the start">⏮ Start</button>
          <button className={btn} onClick={() => act('back')}>↺ Replay part</button>
          <button className={btn + ' !bg-sky-600 px-8'} onClick={() => { if (!started) void start(); act('toggle') }}>{playing || count ? 'Pause' : 'Play'}</button>
          <button className={btn} onClick={() => act('next')}>Next part</button>
          <button className={btn} onClick={() => act('smaller')} aria-label="Smaller text">A−</button>
          <span className="min-w-10 text-center tabular-nums">{prefs.size}</span>
          <button className={btn} onClick={() => act('bigger')} aria-label="Bigger text">A+</button>
          <button className={btn} onClick={() => act('slower')} aria-label="Slower">−</button>
          <span className="min-w-20 text-center tabular-nums">speed {prefs.speed}</span>
          <button className={btn} onClick={() => act('faster')} aria-label="Faster">+</button>
          <button className={btn + (prefs.mirror ? ' !bg-sky-600' : '')} onClick={() => act('mirror')}>Mirror</button>
          <button className={btn + (prefs.flip ? ' !bg-sky-600' : '')} onClick={() => act('flip')}>Flip</button>
          <button className={btn + (prefs.countdown ? ' !bg-sky-600' : '')} onClick={() => setPref({ countdown: !prefs.countdown })}>3-2-1</button>
          <button className={btn} onClick={() => setRemote(true)}>Remote</button>
          <button className={btn} onClick={() => { void document.exitFullscreen?.().catch(() => {}); window.location.hash = '#/film' }}>Close</button>
        </div>
      )}
      {remote && <RemoteSetup learning={learning} press={press} onClose={() => setRemote(false)} />}
    </div>
  )
}

function RemoteSetup({ learning, press, onClose }: {
  learning: React.MutableRefObject<((key: string) => void) | null>
  press: (key: string) => boolean
  onClose: () => void
}) {
  const [want, setWant] = useState<Act | null>(null)
  const [heard, setHeard] = useState('')
  const [keys, setKeys] = useState(loadKeys)
  const [note, setNote] = useState('')
  useEffect(() => {
    learning.current = (key) => {
      setHeard(key)
      if (want) {
        learnKey(key, want)
        setKeys(loadKeys())
        setWant(null)
      }
    }
    return () => {
      learning.current = null
    }
  }, [want, learning])
  const pair = async () => {
    setNote('')
    try {
      setNote(`Connected: ${await pairBluetooth(press, () => setNote('The remote disconnected. Pair it again.'))}. Press its buttons.`)
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    }
  }
  return (
    <div className="absolute inset-0 overflow-y-auto bg-black/95 p-6">
      <div className="mx-auto max-w-[760px]">
        <h2 className="mb-1 text-[26px] font-semibold">Remote</h2>
        <p className="mb-4 text-[17px] text-white/70">
          {want ? 'Now press the button on your remote.' : 'Tap what a button should do, then press that button.'} Last press:{' '}
          <b className="text-white">{heard || 'none yet'}</b>
        </p>
        {ACTS.map((a) => (
          <button key={a.id} onClick={(e) => { e.currentTarget.blur(); setWant(a.id) }}
            className={`mb-2 flex min-h-14 w-full items-center justify-between rounded-xl px-4 text-left text-[18px] ${want === a.id ? 'bg-sky-600' : 'bg-white/10'}`}>
            <span>{a.label}</span>
            <span className="max-w-[55%] truncate text-[14px] text-white/60">
              {Object.entries(keys).filter(([, v]) => v === a.id).map(([k]) => (k === ' ' ? 'Space' : k)).join(', ')}
            </span>
          </button>
        ))}
        <div className="mt-3 flex flex-wrap gap-2">
          {bluetoothReady() && <button className="h-14 rounded-xl bg-white/12 px-4 text-[17px]" onClick={() => void pair()}>Pair a Bluetooth remote here</button>}
          <button className="h-14 rounded-xl bg-white/12 px-4 text-[17px]" onClick={() => { forgetKeys(); setKeys(loadKeys()) }}>Reset buttons</button>
          <button className="h-14 rounded-xl !bg-sky-600 px-6 text-[17px]" onClick={onClose}>Done</button>
        </div>
        {note && <p className="mt-3 text-[17px] text-amber-300">{note}</p>}
      </div>
    </div>
  )
}
