import { useEffect, useState } from 'react'
import { Trash2, Folder, FolderPlus, ChevronUp, Check } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'

interface Share {
  name: string
  server: string
  share: string
  user?: string
  mount: string
}
interface DirResp {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
}

const BLANK_SHARE = { name: '', server: '', share: '', user: 'guest', password: '', mount: '' }

/** Allowed working folders (a directory picker over the container's filesystem)
 *  plus SMB share mounts. Backed by /api/v1/paths, /api/v1/system/dirs and
 *  /api/v1/smb/shares. */
export function FilesystemManager() {
  const [paths, setPaths] = useState<string[]>([])
  const [shares, setShares] = useState<Share[]>([])
  const [browse, setBrowse] = useState<DirResp | null>(null)
  const [browsing, setBrowsing] = useState(false)
  const [share, setShare] = useState({ ...BLANK_SHARE })
  const [msg, setMsg] = useState('')

  async function load() {
    try {
      const [p, s] = await Promise.all([
        api.get<{ paths: string[] }>('/api/v1/paths'),
        api.get<{ shares: Share[] }>('/api/v1/smb/shares'),
      ])
      setPaths(p.paths ?? [])
      setShares(s.shares ?? [])
    } catch (e) {
      setMsg(String(e))
    }
  }
  useEffect(() => {
    void load()
  }, [])

  async function openBrowser(path = '/') {
    setBrowsing(true)
    try {
      setBrowse(await api.get<DirResp>(`/api/v1/system/dirs?path=${encodeURIComponent(path)}`))
    } catch (e) {
      setMsg(String(e))
    }
  }

  async function addPath(p: string) {
    try {
      const r = await api.post<{ paths: string[] }>('/api/v1/paths', { path: p })
      setPaths(r.paths ?? [])
    } catch (e) {
      setMsg(String(e))
    }
  }
  async function removePath(p: string) {
    try {
      const r = await api.post<{ paths: string[] }>('/api/v1/paths/remove', { path: p })
      setPaths(r.paths ?? [])
    } catch (e) {
      setMsg(String(e))
    }
  }

  async function addShare() {
    if (!share.name.trim() || !share.server.trim() || !share.share.trim() || !share.mount.trim()) {
      setMsg('Name, server, share and mount point are required.')
      return
    }
    try {
      await api.post('/api/v1/smb/shares', { ...share })
      setShare({ ...BLANK_SHARE })
      await load()
    } catch (e) {
      setMsg(String(e))
    }
  }
  async function removeShare(name: string) {
    try {
      await api.post('/api/v1/smb/shares/remove', { name })
      await load()
    } catch (e) {
      setMsg(String(e))
    }
  }

  return (
    <div className="space-y-5">
      {/* Allowed working folders */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[12px] font-semibold text-ink-2">Allowed working folders</div>
          <Button variant="ghost" size="sm" onClick={() => openBrowser('/')}>
            <FolderPlus className="mr-1 size-4" /> Browse & add
          </Button>
        </div>
        <p className="mb-2 text-[12px] text-ink-3">
          Folders inside the container that tools may read and write. Browse the filesystem to add one instead of
          typing a path.
        </p>
        {paths.length === 0 ? (
          <p className="text-[12.5px] text-ink-3">No folders allowed yet.</p>
        ) : (
          <ul className="space-y-1">
            {paths.map((p) => (
              <li
                key={p}
                className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-[12.5px] text-ink-1"
              >
                <Folder className="size-4 text-ink-3" />
                <span className="font-mono">{p}</span>
                <Button variant="ghost" size="icon-sm" className="ml-auto" title="Remove" onClick={() => removePath(p)}>
                  <Trash2 className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        {browsing && browse && (
          <div className="mt-3 rounded-lg border border-line">
            <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-[12px]">
              <Button
                variant="ghost"
                size="icon-sm"
                title="Up"
                disabled={!browse.parent}
                onClick={() => browse.parent && openBrowser(browse.parent)}
              >
                <ChevronUp className="size-4" />
              </Button>
              <span className="font-mono text-ink-2">{browse.path}</span>
              <Button variant="primary" size="sm" className="ml-auto" onClick={() => addPath(browse.path)}>
                <Check className="mr-1 size-4" /> Allow this folder
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setBrowsing(false)}>
                Close
              </Button>
            </div>
            <ul className="max-h-56 overflow-auto">
              {browse.dirs.length === 0 ? (
                <li className="px-3 py-2 text-[12px] text-ink-3">No sub-folders.</li>
              ) : (
                browse.dirs.map((d) => (
                  <li key={d.path}>
                    <button
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] text-ink-1 hover:bg-surface-2"
                      onClick={() => openBrowser(d.path)}
                    >
                      <Folder className="size-4 text-ink-3" />
                      {d.name}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        )}
      </section>

      {/* SMB shares */}
      <section>
        <div className="mb-2 text-[12px] font-semibold text-ink-2">SMB / network shares</div>
        {shares.length > 0 && (
          <div className="mb-3 overflow-hidden rounded-lg border border-line">
            <table className="w-full text-[12.5px]">
              <thead className="bg-surface-2 text-ink-3">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Name</th>
                  <th className="px-3 py-2 text-left font-medium">Location</th>
                  <th className="px-3 py-2 text-left font-medium">Mount</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {shares.map((s) => (
                  <tr key={s.name} className="border-t border-line">
                    <td className="px-3 py-2 font-medium text-ink-1">{s.name}</td>
                    <td className="px-3 py-2 font-mono text-ink-2">
                      \\{s.server}\{s.share}
                    </td>
                    <td className="px-3 py-2 font-mono text-ink-2">{s.mount}</td>
                    <td className="px-3 py-2 text-right">
                      <Button variant="ghost" size="icon-sm" title="Remove" onClick={() => removeShare(s.name)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="rounded-lg border border-line p-3">
          <div className="mb-2 text-[12px] font-semibold text-ink-2">Add a share</div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name">
              <Input value={share.name} onChange={(e) => setShare({ ...share, name: e.target.value })} placeholder="media" />
            </Field>
            <Field label="Server">
              <Input value={share.server} onChange={(e) => setShare({ ...share, server: e.target.value })} placeholder="192.168.1.5" />
            </Field>
            <Field label="Share">
              <Input value={share.share} onChange={(e) => setShare({ ...share, share: e.target.value })} placeholder="Media" />
            </Field>
            <Field label="Mount point" hint="In-container path">
              <Input value={share.mount} onChange={(e) => setShare({ ...share, mount: e.target.value })} placeholder="/mnt/media" />
            </Field>
            <Field label="User">
              <Input value={share.user} onChange={(e) => setShare({ ...share, user: e.target.value })} placeholder="guest" />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={share.password}
                onChange={(e) => setShare({ ...share, password: e.target.value })}
                placeholder="••••••"
              />
            </Field>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" size="sm" onClick={addShare}>
              Add share
            </Button>
            {msg && <span className="text-[12px] text-ink-3">{msg}</span>}
          </div>
        </div>
      </section>
    </div>
  )
}
