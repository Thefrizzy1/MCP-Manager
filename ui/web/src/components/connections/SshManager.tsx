import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'

interface Host {
  name: string
  host: string
  user?: string
  port?: number
  readonly?: boolean
  key?: string
  password?: string
}

const BLANK = { name: '', host: '', user: 'root', port: '22', password: '', key_path: '', readonly: true }

/** Manage SSH hosts (name + credentials). Backed by /api/v1/ssh/hosts, stored as
 *  the SSH_HOSTS JSON array the ssh_* tools read. */
export function SshManager() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [f, setF] = useState({ ...BLANK })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const d = await api.get<{ hosts: Host[] }>('/api/v1/ssh/hosts')
      setHosts(d.hosts ?? [])
    } catch (e) {
      setMsg(String(e))
    }
  }
  useEffect(() => {
    void load()
  }, [])

  async function add() {
    if (!f.name.trim() || !f.host.trim()) {
      setMsg('Name and host are required.')
      return
    }
    setBusy(true)
    setMsg('')
    try {
      await api.post('/api/v1/ssh/hosts', {
        name: f.name.trim(),
        host: f.host.trim(),
        user: f.user.trim() || 'root',
        port: Number(f.port) || 22,
        password: f.password || null,
        key_path: f.key_path || null,
        readonly: f.readonly,
      })
      setF({ ...BLANK })
      await load()
    } catch (e) {
      setMsg(String(e))
    }
    setBusy(false)
  }

  async function remove(name: string) {
    try {
      await api.post('/api/v1/ssh/hosts/remove', { name })
      await load()
    } catch (e) {
      setMsg(String(e))
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-3">
        Add SSH hosts with credentials. Read-only hosts allow only allowlisted commands; enable write per host.
      </p>

      {hosts.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-[12.5px]">
            <thead className="bg-surface-2 text-ink-3">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Name</th>
                <th className="px-3 py-2 text-left font-medium">Host</th>
                <th className="px-3 py-2 text-left font-medium">User</th>
                <th className="px-3 py-2 text-left font-medium">Auth</th>
                <th className="px-3 py-2 text-left font-medium">Access</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {hosts.map((h) => (
                <tr key={h.name} className="border-t border-line">
                  <td className="px-3 py-2 font-medium text-ink-1">{h.name}</td>
                  <td className="px-3 py-2 text-ink-2">
                    {h.host}:{h.port ?? 22}
                  </td>
                  <td className="px-3 py-2 text-ink-2">{h.user ?? 'root'}</td>
                  <td className="px-3 py-2 text-ink-3">{h.key ? 'key' : h.password ? 'password' : '—'}</td>
                  <td className="px-3 py-2 text-ink-3">{h.readonly === false ? 'read/write' : 'read-only'}</td>
                  <td className="px-3 py-2 text-right">
                    <Button variant="ghost" size="icon-sm" title="Remove" onClick={() => remove(h.name)}>
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
        <div className="mb-2 text-[12px] font-semibold text-ink-2">Add a host</div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="nas" />
          </Field>
          <Field label="Host / IP">
            <Input value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} placeholder="192.168.1.5" />
          </Field>
          <Field label="User">
            <Input value={f.user} onChange={(e) => setF({ ...f, user: e.target.value })} placeholder="root" />
          </Field>
          <Field label="Port">
            <Input value={f.port} onChange={(e) => setF({ ...f, port: e.target.value })} placeholder="22" />
          </Field>
          <Field label="Password" hint="Or use a key path below.">
            <Input
              type="password"
              value={f.password}
              onChange={(e) => setF({ ...f, password: e.target.value })}
              placeholder="••••••"
            />
          </Field>
          <Field label="Private key path" hint="In-container path, e.g. /root/.ssh/id_ed25519">
            <Input value={f.key_path} onChange={(e) => setF({ ...f, key_path: e.target.value })} placeholder="/root/.ssh/id_ed25519" />
          </Field>
        </div>
        <label className="mt-3 flex items-center gap-2 text-[12.5px] text-ink-2">
          <input
            type="checkbox"
            checked={!f.readonly}
            onChange={(e) => setF({ ...f, readonly: !e.target.checked })}
          />
          Allow write commands (default is read-only)
        </label>
        <div className="mt-3 flex items-center gap-3">
          <Button variant="primary" size="sm" disabled={busy} onClick={add}>
            Add host
          </Button>
          {msg && <span className="text-[12px] text-ink-3">{msg}</span>}
        </div>
      </div>
    </div>
  )
}
