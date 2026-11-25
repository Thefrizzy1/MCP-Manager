/** Teleprompter remotes. A button is whatever the device sends: a key name for
 *  keyboard-style remotes, or "bt:<bytes>" for a remote read over Web Bluetooth. */
export type Act = 'toggle' | 'next' | 'back' | 'restart' | 'faster' | 'slower' | 'bigger' | 'smaller'

export const ACTS: { id: Act; label: string }[] = [
  { id: 'toggle', label: 'Play / pause' },
  { id: 'back', label: 'Replay this part' },
  { id: 'next', label: 'Next part' },
  { id: 'restart', label: 'Replay from the start' },
  { id: 'faster', label: 'Faster' },
  { id: 'slower', label: 'Slower' },
  { id: 'bigger', label: 'Bigger text' },
  { id: 'smaller', label: 'Smaller text' },
]

const DEFAULTS: Record<string, Act> = {
  ' ': 'toggle', Enter: 'toggle', MediaPlayPause: 'toggle', MediaPlay: 'toggle', MediaPause: 'toggle',
  ArrowRight: 'next', PageDown: 'next', MediaTrackNext: 'next',
  ArrowLeft: 'back', PageUp: 'back', MediaTrackPrevious: 'back',
  ArrowUp: 'faster', ArrowDown: 'slower', Home: 'restart', '+': 'bigger', '=': 'bigger', '-': 'smaller',
  AudioVolumeUp: 'faster', AudioVolumeDown: 'slower',
}

export function loadKeys(): Record<string, Act> {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem('prompter-keys') ?? '{}') }
  } catch {
    return DEFAULTS
  }
}

export function learnKey(key: string, act: Act): void {
  try {
    const own = JSON.parse(localStorage.getItem('prompter-keys') ?? '{}')
    localStorage.setItem('prompter-keys', JSON.stringify({ ...own, [key]: act }))
  } catch {
    /* private window: the button works until the page closes */
  }
}

export function forgetKeys(): void {
  try {
    localStorage.removeItem('prompter-keys')
  } catch {
    /* nothing stored */
  }
}

/** The name of a pressed key; remotes that send no name still have a code. */
export const keyName = (e: KeyboardEvent): string =>
  e.key && e.key !== 'Unidentified' ? e.key : e.code || `key${e.keyCode}`

// Services small Bluetooth remotes commonly use; a browser only opens the ones named here.
const SERVICES: (number | string)[] = [
  0xffe0, 0xfff0, 0xff00, 0xffd0, 0xfee0, 0xfee7, 0xae00, 0xae30, 0xabf0, 0x180f,
  '6e400001-b5a3-f393-e0a9-e50e24dcca9e', '0000fe59-0000-1000-8000-00805f9b34fb',
]

interface BtChar extends EventTarget {
  properties: { notify: boolean; indicate: boolean }
  value?: DataView
  startNotifications(): Promise<unknown>
}
interface BtServer { getPrimaryServices(): Promise<{ uuid: string; getCharacteristics(): Promise<BtChar[]> }[]> }
interface BtDevice extends EventTarget { name?: string; gatt?: { connect(): Promise<BtServer> } }

export const bluetoothReady = (): boolean => window.isSecureContext && 'bluetooth' in navigator

/** Pair with a remote directly. Every distinct message it sends counts as a button. */
export async function pairBluetooth(onButton: (key: string) => void, onLost: () => void): Promise<string> {
  const bt = (navigator as unknown as { bluetooth: { requestDevice(o: unknown): Promise<BtDevice> } }).bluetooth
  const device = await bt.requestDevice({ acceptAllDevices: true, optionalServices: SERVICES })
  const server = await device.gatt!.connect()
  let heard = 0
  for (const service of await server.getPrimaryServices()) {
    if (service.uuid.startsWith('0000180f')) continue // battery level is not a button
    for (const c of await service.getCharacteristics()) {
      if (!c.properties.notify && !c.properties.indicate) continue
      c.addEventListener('characteristicvaluechanged', () => {
        const v = c.value
        if (!v) return
        const bytes = Array.from(new Uint8Array(v.buffer, v.byteOffset, v.byteLength))
        if (bytes.some((b) => b !== 0)) onButton('bt:' + bytes.map((b) => b.toString(16).padStart(2, '0')).join(''))
      })
      await c.startNotifications()
      heard++
    }
  }
  if (!heard) throw new Error('This remote opens nothing a browser can listen to, so it only works in its own app.')
  device.addEventListener('gattserverdisconnected', onLost)
  return device.name || 'Bluetooth remote'
}
