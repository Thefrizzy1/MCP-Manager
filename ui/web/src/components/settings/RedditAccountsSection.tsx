import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Star, Trash2 } from 'lucide-react'

import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { Tag } from '@/components/ui/Tag'
import { useToast } from '@/components/ui/Toast'

/**
 * Several Reddit logins, managed from the dashboard.
 *
 * The store and the API for this shipped without any UI, so the only way to add
 * a second Reddit account was to POST to the endpoint by hand — which is not a
 * feature, it is a note to a future developer.
 *
 * Secrets are write-only here on purpose: the API never returns them, so the
 * fields are for entering a value, never for showing you the one on file.
 */
interface RedditAccount {
  id: string
  label: string
  username: string
  from_env: boolean
  is_default: boolean
}
interface AccountsResp {
  accounts: RedditAccount[]
  default: string
}

export function RedditAccountsSection() {
  const toast = useToast()
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['reddit-accounts'],
    queryFn: () => api.get<AccountsResp>('/api/v1/reddit/accounts'),
  })
  const [busy, setBusy] = useState('')
  const [open, setOpen] = useState(false)

  const accounts = q.data?.accounts ?? []

  async function call(fn: () => Promise<unknown>, key: string, ok: string) {
    setBusy(key)
    try {
      await fn()
      toast.success(ok)
      qc.invalidateQueries({ queryKey: ['reddit-accounts'] })
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <Card>
      <CardHeader
        title="Reddit accounts"
        subtitle="Read as a specific identity — subscriptions, saved posts, the front page"
        action={
          <Button variant="default" size="sm" onClick={() => setOpen((v) => !v)}>
            {open ? 'Cancel' : 'Add account'}
          </Button>
        }
      />
      <div className="px-4 pb-3">
        {accounts.length === 0 && !open && (
          <p className="py-2 text-[12.5px] text-ink-3">
            No Reddit login yet. The social tools still read public feeds without one —
            titles and links, no scores, and none of your own subscriptions.
          </p>
        )}

        {accounts.map((a) => (
          <div
            key={a.id}
            className="flex items-center gap-2 border-b border-border py-2 last:border-0"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-[13px] text-ink">{a.label}</span>
                {a.is_default && <Tag>default</Tag>}
                {a.from_env && <Tag>.env</Tag>}
              </div>
              <div className="text-[11.5px] text-ink-3">u/{a.username}</div>
            </div>

            {!a.is_default && (
              <Button
                variant="ghost"
                size="sm"
                title={`Use ${a.label} when a tool does not name an account`}
                aria-label={`Make ${a.label} the default`}
                disabled={busy === a.id}
                onClick={() =>
                  call(
                    () => api.post(`/api/v1/reddit/accounts/${a.id}/default`),
                    a.id,
                    `${a.label} is now the default.`,
                  )
                }
              >
                <Star size={13} />
              </Button>
            )}

            <Button
              variant="ghost"
              size="sm"
              title={
                a.from_env
                  ? 'This one comes from REDDIT_* in .env — clear it there'
                  : `Remove ${a.label}`
              }
              aria-label={`Remove ${a.label}`}
              disabled={a.from_env || busy === a.id}
              onClick={() => {
                if (confirm(`Remove the Reddit account “${a.label}”?`))
                  call(
                    () => api.del(`/api/v1/reddit/accounts/${a.id}`),
                    a.id,
                    `${a.label} removed.`,
                  )
              }}
            >
              <Trash2 size={13} />
            </Button>
          </div>
        ))}

        {open && (
          <AddAccount
            busy={busy === 'add'}
            onCancel={() => setOpen(false)}
            onSubmit={async (body) => {
              await call(() => api.post('/api/v1/reddit/accounts', body), 'add', 'Account added.')
              setOpen(false)
            }}
          />
        )}
      </div>
    </Card>
  )
}

function AddAccount({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean
  onCancel: () => void
  onSubmit: (body: Record<string, string>) => Promise<void>
}) {
  const [f, setF] = useState({
    label: '',
    username: '',
    password: '',
    client_id: '',
    client_secret: '',
  })
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF((prev) => ({ ...prev, [k]: e.target.value }))

  // All four or nothing: a partial script app authenticates as nobody and fails
  // at the first private endpoint rather than here, which is the wrong place to
  // find out.
  const ready = f.username && f.password && f.client_id && f.client_secret

  return (
    <div className="mt-3 space-y-2.5 rounded-[var(--radius-sm)] border border-border bg-surface-2 p-3">
      <p className="text-[12px] text-ink-3">
        Create a <span className="text-ink-2">script</span> app at{' '}
        <a
          className="text-accent hover:underline"
          href="https://www.reddit.com/prefs/apps"
          target="_blank"
          rel="noreferrer noopener"
        >
          reddit.com/prefs/apps
        </a>
        , then paste its client id and secret with the account's own login.
      </p>
      <div className="grid gap-2.5 sm:grid-cols-2">
        <Field label="Name" hint="What you call this account here">
          <Input value={f.label} onChange={set('label')} placeholder="Personal" />
        </Field>
        <Field label="Reddit username">
          <Input value={f.username} onChange={set('username')} placeholder="your_username" />
        </Field>
        <Field label="Reddit password">
          <Input type="password" value={f.password} onChange={set('password')} />
        </Field>
        <Field label="Client id">
          <Input value={f.client_id} onChange={set('client_id')} />
        </Field>
        <Field label="Client secret" hint="Stored on the server; never shown again">
          <Input type="password" value={f.client_secret} onChange={set('client_secret')} />
        </Field>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={!ready || busy}
          onClick={() => onSubmit({ ...f, label: f.label || f.username })}
        >
          Add account
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        {!ready && (
          <span className="text-[11.5px] text-ink-3">
            All four fields are needed — a partial app authenticates as nobody.
          </span>
        )}
      </div>
    </div>
  )
}
