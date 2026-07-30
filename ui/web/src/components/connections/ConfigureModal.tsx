import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { SshManager } from './SshManager'
import { FilesystemManager } from './FilesystemManager'
import { PublicApisManager } from './PublicApisManager'

interface ConfigField {
  key: string
  label: string
  value?: string
  placeholder?: string
  secret?: boolean
  set?: boolean
}
interface ConfigResp {
  label: string
  icon?: string
  url?: string
  fields: ConfigField[]
  manager?: string
  documentation_url?: string
}

export function ConfigureModal({
  id,
  onClose,
  onSaved,
}: {
  id: string
  onClose: () => void
  onSaved: () => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['service-config', id],
    queryFn: () => api.get<ConfigResp>(`/api/v1/service/${id}/config`),
  })
  const [values, setValues] = useState<Record<string, string>>({})
  const [cleared, setCleared] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) {
      const init: Record<string, string> = {}
      for (const f of data.fields) init[f.key] = f.secret ? '' : (f.value ?? '')
      setValues(init)
      setCleared({})
    }
  }, [data])

  async function save() {
    // Only send changed values. A blank secret box means "keep current" (we never
    // render the stored value), so removing a secret needs the explicit Remove
    // action below — it sends "", which the backend treats as delete.
    const body: Record<string, string> = {}
    for (const f of data?.fields ?? []) {
      const v = values[f.key] ?? ''
      if (cleared[f.key]) {
        body[f.key] = ''
      } else if (f.secret) {
        if (v !== '') body[f.key] = v
      } else if (v !== (f.value ?? '')) {
        body[f.key] = v
      }
    }
    if (Object.keys(body).length === 0) {
      setMsg('Nothing to save.')
      return
    }
    setSaving(true)
    setMsg('Saving…')
    try {
      await api.post('/env/save', body)
      onSaved()
    } catch (e) {
      setMsg(String(e))
      setSaving(false)
    }
  }

  const manager = data?.manager
  const isManaged = manager === 'ssh' || manager === 'filesystem' || manager === 'public_apis'

  return (
    <Modal
      open
      onClose={onClose}
      title={`Configure ${data?.label ?? id}`}
      footer={
        <>
          {msg && <span className="mr-auto text-[12px] text-ink-3">{msg}</span>}
          {data?.documentation_url && (
            <a href={data.documentation_url} target="_blank" rel="noopener noreferrer">
              <Button variant="ghost" size="sm">
                Docs ↗
              </Button>
            </a>
          )}
          {isManaged ? (
            <Button variant="primary" size="sm" onClick={onClose}>
              Done
            </Button>
          ) : (
            <Button variant="primary" size="sm" disabled={saving} onClick={save}>
              Save
            </Button>
          )}
        </>
      }
    >
      {isLoading ? (
        <p className="text-[13px] text-ink-3">Loading…</p>
      ) : manager === 'ssh' ? (
        <SshManager />
      ) : manager === 'filesystem' ? (
        <FilesystemManager />
      ) : manager === 'public_apis' ? (
        <PublicApisManager />
      ) : !data?.fields?.length ? (
        <p className="text-[13px] text-ink-3">
          This service works out of the box — there's nothing to configure. Use <strong>Test</strong> to check it's
          reachable.
        </p>
      ) : (
        <div className="space-y-3">
          {data.fields.map((f) => (
            <Field key={f.key} label={f.label}>
              <div className="flex items-center gap-2">
                <Input
                  className="flex-1"
                  type={f.secret ? 'password' : 'text'}
                  value={cleared[f.key] ? '' : (values[f.key] ?? '')}
                  disabled={!!cleared[f.key]}
                  placeholder={
                    cleared[f.key]
                      ? 'will be removed on save'
                      : f.secret && f.set
                        ? '•••••• set — blank keeps it'
                        : f.placeholder
                  }
                  onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                />
                {(f.set || (f.value ?? '') !== '') && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setCleared((c) => ({ ...c, [f.key]: !c[f.key] }))}
                  >
                    {cleared[f.key] ? 'Undo' : 'Remove'}
                  </Button>
                )}
              </div>
            </Field>
          ))}
        </div>
      )}
    </Modal>
  )
}
