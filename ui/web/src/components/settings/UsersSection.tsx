import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, KeyRound } from 'lucide-react'
import { api } from '@/lib/api'
import { useWhoami, type UiUser } from '@/lib/auth'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Row } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'

/** Account (change my own password) + admin-only user management. Replaces the
 *  old .env UI_USERNAME/UI_PASSWORD rows, which the user store now supersedes. */
export function UsersSection() {
  const toast = useToast()
  const qc = useQueryClient()
  const who = useWhoami()
  const isAdmin = who.data?.role === 'admin'

  return (
    <>
      <Card>
        <CardHeader title="Your account" subtitle={who.data ? `Signed in as ${who.data.username}` : 'Change your password'} />
        <div className="px-4 pb-3">
          <ChangeOwnPassword
            onDone={() => qc.invalidateQueries({ queryKey: ['whoami'] })}
            toast={toast}
          />
        </div>
      </Card>

      {isAdmin && (
        <Card>
          <CardHeader title="Users" subtitle="Add or remove dashboard accounts" />
          <div className="px-4 pb-3">
            <UsersAdmin toast={toast} qc={qc} currentUser={who.data?.username ?? ''} />
          </div>
        </Card>
      )}
    </>
  )
}

type Toast = ReturnType<typeof useToast>

function ChangeOwnPassword({ onDone, toast }: { onDone: () => void; toast: Toast }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (next.length < 8) {
      toast.error('New password must be at least 8 characters.')
      return
    }
    setBusy(true)
    try {
      await api.post('/api/v1/auth/change-password', { current_password: current, new_password: next })
      toast.success('Password changed.')
      setCurrent('')
      setNext('')
      onDone()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Row label="Current password">
        <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
      </Row>
      <Row label="New password" hint="min 8 characters">
        <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        <Button variant="primary" size="sm" onClick={submit} disabled={busy || !current || !next}>
          {busy ? 'Saving…' : 'Change password'}
        </Button>
      </Row>
    </>
  )
}

function UsersAdmin({ toast, qc, currentUser }: { toast: Toast; qc: ReturnType<typeof useQueryClient>; currentUser: string }) {
  const users = useQuery({ queryKey: ['ui-users'], queryFn: () => api.get<{ users: UiUser[] }>('/api/v1/auth/users') })
  const [nu, setNu] = useState('')
  const [np, setNp] = useState('')
  const [nr, setNr] = useState<'user' | 'admin'>('user')

  const refetch = () => {
    users.refetch()
    qc.invalidateQueries({ queryKey: ['whoami'] })
  }

  async function addUser() {
    if (!nu.trim() || np.length < 8) {
      toast.error('Username required and password must be at least 8 characters.')
      return
    }
    try {
      await api.post('/api/v1/auth/users', { username: nu.trim(), password: np, role: nr })
      toast.success(`Added ${nu.trim()}.`)
      setNu('')
      setNp('')
      setNr('user')
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function removeUser(username: string) {
    if (!confirm(`Remove user “${username}”? This cannot be undone.`)) return
    try {
      await api.del(`/api/v1/auth/users/${encodeURIComponent(username)}`)
      toast.success(`Removed ${username}.`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function resetPassword(username: string) {
    const pw = prompt(`New password for “${username}” (min 8 chars):`)
    if (pw == null) return
    if (pw.length < 8) {
      toast.error('Password must be at least 8 characters.')
      return
    }
    try {
      await api.post(`/api/v1/auth/users/${encodeURIComponent(username)}/password`, { new_password: pw })
      toast.success(`Password reset for ${username}.`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  return (
    <>
      <div className="mb-3 divide-y divide-border rounded-[var(--radius)] border border-border">
        {(users.data?.users ?? []).map((u) => (
          <div key={u.username} className="flex items-center gap-3 px-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-[13px] font-medium text-ink">{u.username}</span>
                {u.is_default && (
                  <span className="rounded-full bg-warn-weak px-1.5 py-px text-[10px] font-medium text-warn">
                    default password
                  </span>
                )}
              </div>
              <div className="text-[11px] uppercase tracking-wider text-ink-3">{u.role}</div>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => resetPassword(u.username)}
                title="Reset password"
                aria-label={`Reset password for ${u.username}`}
                className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] text-ink-3 hover:bg-surface-hover hover:text-ink"
              >
                <KeyRound size={15} />
              </button>
              <button
                onClick={() => removeUser(u.username)}
                disabled={u.username === currentUser}
                title={u.username === currentUser ? 'You cannot remove yourself' : 'Remove user'}
                aria-label={`Remove ${u.username}`}
                className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] text-ink-3 hover:bg-danger-weak hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))}
        {users.data && users.data.users.length === 0 && (
          <div className="px-3 py-2 text-[12px] text-ink-3">No users.</div>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <Input placeholder="username" value={nu} onChange={(e) => setNu(e.target.value)} className="max-w-[160px]" />
        <Input
          type="password"
          placeholder="password (min 8)"
          value={np}
          onChange={(e) => setNp(e.target.value)}
          className="max-w-[180px]"
          autoComplete="new-password"
        />
        <select
          value={nr}
          onChange={(e) => setNr(e.target.value as 'user' | 'admin')}
          className="h-[34px] rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 text-[13px] text-ink"
        >
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <Button variant="primary" size="sm" onClick={addUser}>
          Add user
        </Button>
      </div>
    </>
  )
}
