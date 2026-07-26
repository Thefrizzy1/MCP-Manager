import { Modal } from '@/components/ui/Modal'
import { ServiceLogo } from '@/components/ui/ServiceLogo'

// Homelab-focused starting points. Picking one pre-fills the Add form — you
// still supply the URL and key. Ids match brand logos where we have them.
const CATALOG: { group: string; items: { id: string; name: string }[] }[] = [
  {
    group: 'Media',
    items: [
      { id: 'plex', name: 'Plex' },
      { id: 'audiobookshelf', name: 'Audiobookshelf' },
      { id: 'kavita', name: 'Kavita' },
      { id: 'navidrome', name: 'Navidrome' },
      { id: 'tautulli', name: 'Tautulli' },
    ],
  },
  {
    group: 'Downloads / *arr',
    items: [
      { id: 'prowlarr', name: 'Prowlarr' },
      { id: 'bazarr', name: 'Bazarr' },
      { id: 'sabnzbd', name: 'SABnzbd' },
      { id: 'transmission', name: 'Transmission' },
    ],
  },
  {
    group: 'Management',
    items: [
      { id: 'portainer', name: 'Portainer' },
      { id: 'uptimekuma', name: 'Uptime Kuma' },
      { id: 'grafana', name: 'Grafana' },
      { id: 'prometheus', name: 'Prometheus' },
      { id: 'proxmox', name: 'Proxmox' },
    ],
  },
  {
    group: 'Docs & notes',
    items: [
      { id: 'paperless', name: 'Paperless-ngx' },
      { id: 'bookstack', name: 'BookStack' },
      { id: 'wikijs', name: 'Wiki.js' },
      { id: 'trilium', name: 'Trilium' },
    ],
  },
  {
    group: 'Security & network',
    items: [
      { id: 'vaultwarden', name: 'Vaultwarden' },
      { id: 'pihole', name: 'Pi-hole' },
      { id: 'adguard', name: 'AdGuard Home' },
      { id: 'authelia', name: 'Authelia' },
    ],
  },
  {
    group: 'Dev & storage',
    items: [
      { id: 'gitea', name: 'Gitea' },
      { id: 'forgejo', name: 'Forgejo' },
      { id: 'minio', name: 'MinIO' },
      { id: 'seafile', name: 'Seafile' },
    ],
  },
]

export function CatalogModal({ onClose, onPick }: { onClose: () => void; onPick: (name: string) => void }) {
  return (
    <Modal open onClose={onClose} title="Service catalog" width={640}>
      <p className="mb-3 text-[12.5px] text-ink-3">
        Pick a service to pre-fill a custom connection — you supply its URL and API key.
      </p>
      <div className="space-y-4">
        {CATALOG.map((cat) => (
          <div key={cat.group}>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-3">{cat.group}</div>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {cat.items.map((it) => (
                <button
                  key={it.id}
                  onClick={() => onPick(it.name)}
                  className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-border bg-surface-2 px-2.5 py-1.5 text-left hover:border-border-strong hover:bg-surface-hover"
                >
                  <ServiceLogo id={it.id} label={it.name} size={20} />
                  <span className="truncate text-[12.5px] text-ink">{it.name}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  )
}
