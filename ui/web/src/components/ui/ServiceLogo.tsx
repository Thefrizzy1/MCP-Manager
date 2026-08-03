import { lookupBrandIcon } from '@/lib/serviceIcons'

const COLORS = ['#3a5ce5', '#2e9e5b', '#e08a2e', '#8b5cf6', '#0891b2', '#c026d3', '#0d9488', '#dc2626']

// Category/service emoji fallback for connections with no offline brand glyph.
// Longest-prefix wins; an exact id beats a prefix. Keeps the connection list
// recognisable without a letter tile, and covers services we ship no logo for.
const EMOJI_BY_PREFIX: [string, string][] = [
  ['jellyfin', '🎬'], ['sonarr', '📺'], ['radarr', '🎥'], ['lidarr', '🎵'], ['jellyseerr', '📋'],
  ['qbittorrent', '⬇️'], ['transmission', '⬇️'], ['sabnzbd', '⬇️'], ['tautulli', '📈'],
  ['immich', '📸'], ['photoprism', '📸'],
  ['homeassistant', '🏠'], ['ha_', '🏠'],
  ['nextcloud', '☁️'], ['owncloud', '☁️'], ['filebrowser', '📁'], ['filesystem', '📁'], ['smb', '📁'],
  ['obsidian', '📝'], ['bookstack', '📚'], ['wikijs', '📖'], ['paperless', '📄'], ['stirling', '📄'],
  ['comfyui', '🎨'], ['fal', '✨'], ['huggingface', '🤗'], ['ollama', '🦙'],
  ['github', '🐙'], ['gitlab', '🦊'], ['gitea', '🍵'],
  ['docker', '🐳'], ['portainer', '🐳'], ['omv', '💾'], ['minio', '🪣'], ['traefik', '🚦'],
  ['nginx', '🌐'], ['grafana', '📊'], ['prometheus', '🔥'], ['uptime', '📊'], ['netdata', '📈'],
  ['pihole', '🛡️'], ['adguard', '🛡️'], ['vaultwarden', '🔐'], ['bitwarden', '🔐'],
  ['tailscale', '🌐'], ['syncthing', '🔄'], ['n8n', '⚡'], ['habitica', '🎮'], ['ntfy', '🔔'],
  ['reddit', '👽'], ['mastodon', '🐘'], ['lemmy', '🐭'], ['bluesky', '🦋'], ['hackernews', '🟧'],
  ['stackexchange', '💬'],
  ['weather', '🌤️'], ['maps', '🗺️'], ['currency', '💱'], ['wikipedia', '📚'],
  ['websearch', '🔍'], ['google', '🔎'], ['firecrawl', '🔥'], ['youtube', '▶️'],
  ['smtp', '📧'], ['email', '📧'], ['proton', '📧'], ['matrix', '💬'], ['ghost', '👻'],
  ['fail2ban', '🔒'], ['ssh', '🖥️'], ['agent_db', '🗄️'], ['plutus', '🚦'],
  ['audiobookshelf', '🎧'], ['kavita', '📖'], ['calibre', '📚'], ['mealie', '🍲'], ['homepage', '🧭'],
]

function fallbackEmoji(id: string): string {
  const k = (id || '').toLowerCase()
  let best = ''
  let bestLen = 0
  for (const [p, e] of EMOJI_BY_PREFIX) {
    if ((k === p || k.startsWith(p)) && p.length > bestLen) {
      best = e
      bestLen = p.length
    }
  }
  return best
}

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

/** A service mark: the real brand glyph when we know it (offline, simple-icons),
 *  else a category emoji (the backend's `icon`, or one inferred from the id),
 *  else a coloured initial tile. No external fetch. */
export function ServiceLogo({
  id,
  label,
  size = 26,
  domain,
  emoji,
}: {
  id: string
  label?: string
  size?: number
  domain?: string
  emoji?: string
}) {
  const tileClass =
    'inline-flex shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-border bg-surface-2'

  const icon = lookupBrandIcon(id, domain)
  if (icon) {
    return (
      <span className={tileClass} style={{ width: size, height: size }} aria-hidden>
        <svg
          role="img"
          viewBox="0 0 24 24"
          width={Math.round(size * 0.56)}
          height={Math.round(size * 0.56)}
          fill={`#${icon.hex}`}
        >
          <path d={icon.path} />
        </svg>
      </span>
    )
  }

  const emo = (emoji || '').trim() || fallbackEmoji(id)
  if (emo) {
    return (
      <span
        className={tileClass}
        style={{ width: size, height: size, fontSize: Math.round(size * 0.5), lineHeight: 1 }}
        aria-hidden
      >
        {emo}
      </span>
    )
  }

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
