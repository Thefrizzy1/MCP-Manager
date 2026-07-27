import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { SshManager } from './SshManager'
import { FilesystemManager } from './FilesystemManager'

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
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (data) {
      const init: Record<string, string> = {}
      for (const f of data.fields) init[f.key] = f.secret ? '' : (f.value ?? '')
      setValues(init)
    }
  }, [data])

  async function save() {
    // Only send changed values; blank secret = keep current.
    const body: Record<string, string> = {}
    for (const f of data?.fields ?? []) {
      const v = values[f.key] ?? ''
      if (f.secret) {
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
  const isManaged = manager === 'ssh' || manager === 'filesystem'

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
      ) : !data?.fields?.length ? (
        <p className="text-[13px] text-ink-3">
          This service works out of the box — there's nothing to configure. Use <strong>Test</strong> to check it's
          reachable.
        </p>
      ) : (
        <div className="space-y-3">
          {data.fields.map((f) => (
            <Field key={f.key} label={f.label}>
              <Input
                type={f.secret ? 'password' : 'text'}
                value={values[f.key] ?? ''}
                placeholder={f.secret && f.set ? '•••••• set — blank keeps it' : f.placeholder}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
            </Field>
          ))}
        </div>
      )}
    </Modal>
  )
}
