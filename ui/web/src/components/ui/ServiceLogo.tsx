const COLORS = ['#3a5ce5', '#2e9e5b', '#e08a2e', '#8b5cf6', '#0891b2', '#c026d3', '#0d9488', '#dc2626']

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

/** A coloured initial tile for a service (no emojis, no external fetch). */
export function ServiceLogo({ id, label, size = 26 }: { id: string; label?: string; size?: number }) {
  const color = COLORS[hash(id) % COLORS.length]
  const initial = (label || id).replace(/[^a-zA-Z0-9]/g, '').charAt(0).toUpperCase() || '?'
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-[var(--radius-sm)] font-semibold text-white"
      style={{ width: size, height: size, background: color, fontSize: Math.round(size * 0.42) }}
      aria-hidden
    >
      {initial}
    </span>
  )
}
