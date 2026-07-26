import { useState } from 'react'
import { api } from '@/lib/api'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'

interface Integrations {
  version?: number
  integrations?: Array<Record<string, unknown>>
}

export function AddConnectionModal({
  onClose,
  onAdded,
  initialLabel = '',
}: {
  onClose: () => void
  onAdded: () => void
  initialLabel?: string
}) {
  const [label, setLabel] = useState(initialLabel)
  const [id, setId] = useState(initialLabel.toLowerCase().replace(/[^a-z0-9_]/g, '_'))
  const [urlEnv, setUrlEnv] = useState('')
  const [url, setUrl] = useState('')
  const [healthPath, setHealthPath] = useState('/')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  async function save() {
    const slug = id.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_')
    const name = label.trim()
    if (!slug || !name) {
      setMsg('Name and id are required.')
      return
    }
    let env = (urlEnv.trim() || slug.toUpperCase() + '_URL').toUpperCase().replace(/[^A-Z0-9_]/g, '_')
    if (!/^[A-Z]/.test(env)) env = 'X_' + env
    setSaving(true)
    setMsg('Saving…')
    try {
      const full = await api.get<Integrations>('/settings/custom-integrations')
      const list = Array.isArray(full.integrations) ? full.integrations : []
      list.push({
        id: slug,
        label: name,
        description: name,
        url_env: env,
        url_placeholder: url.trim(),
        health_path: healthPath.trim() || '/',
      })
      await api.post('/settings/custom-integrations', { version: full.version || 1, integrations: list })
      if (url.trim()) await api.post('/env/save', { [env]: url.trim() })
      onAdded()
    } catch (e) {
      setMsg(String(e))
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add connection"
      footer={
        <>
          {msg && <span className="mr-auto text-[12px] text-ink-3">{msg}</span>}
          <Button variant="primary" size="sm" disabled={saving} onClick={save}>
            Add connection
          </Button>
        </>
      }
    >
      <p className="mb-3 text-[12.5px] text-ink-3">
        Add a custom service card (base-URL env + health path). Stored in data/custom_integrations.json.
      </p>
      <div className="space-y-3">
        <Field label="Name">
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Audiobookshelf" />
        </Field>
        <Field label="Id (slug)">
          <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="audiobookshelf" />
        </Field>
        <Field label="Env key (URL)" hint="Leave blank to auto-generate from the id.">
          <Input value={urlEnv} onChange={(e) => setUrlEnv(e.target.value)} placeholder="AUDIOBOOKSHELF_URL" />
        </Field>
        <Field label="URL">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://192.168.1.111:13378" />
        </Field>
        <Field label="Health path">
          <Input value={healthPath} onChange={(e) => setHealthPath(e.target.value)} placeholder="/" />
        </Field>
      </div>
    </Modal>
  )
}
