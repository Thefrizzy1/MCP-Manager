import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'

interface ClientCfg {
  id: string
  label: string
  content: string
  mime?: string
  download_name?: string
}
interface Exports {
  clients?: ClientCfg[]
}

export function ClientExportModal({ onClose }: { onClose: () => void }) {
  const [withToken, setWithToken] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const { data } = useQuery({
    queryKey: ['mcp-connections', withToken],
    queryFn: () => api.get<Exports>(`/api/v1/mcp/connections${withToken ? '?include_token=1' : ''}`),
  })
  const clients = data?.clients ?? []
  const current = clients.find((c) => c.id === selected)

  const [test, setTest] = useState<{ ok?: boolean; detail?: string } | null>(null)
  const [testing, setTesting] = useState(false)

  async function testConnection() {
    setTesting(true)
    setTest(null)
    try {
      setTest(await api.get<{ ok?: boolean; detail?: string }>('/api/v1/mcp/selftest'))
    } catch (e) {
      setTest({ ok: false, detail: String(e) })
    } finally {
      setTesting(false)
    }
  }

  function download() {
    if (!current) return
    const blob = new Blob([current.content], { type: (current.mime || 'text/plain') + ';charset=utf-8' })
    const u = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = u
    a.download = current.download_name || 'mcp.json'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(u), 1000)
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Connect a client"
      width={640}
      footer={
        <>
          {test && (
            <span className={`mr-auto text-[12px] ${test.ok ? 'text-ok' : 'text-danger'}`}>
              {test.ok ? 'Endpoint reachable' : test.detail || 'Unreachable'}
            </span>
          )}
          <Button variant="default" size="sm" disabled={testing} onClick={testConnection}>
            {testing ? 'Testing…' : 'Test connection'}
          </Button>
          {current && (
            <Button variant="primary" size="sm" onClick={download}>
              Download
            </Button>
          )}
        </>
      }
    >
      <label className="mb-3 flex items-center gap-1.5 text-[12.5px] text-ink-2">
        <input type="checkbox" checked={withToken} onChange={(e) => setWithToken(e.target.checked)} />
        Embed bearer token
      </label>
      <div className="flex flex-wrap gap-1.5">
        {clients.map((c) => (
          <Button
            key={c.id}
            variant={selected === c.id ? 'primary' : 'default'}
            size="sm"
            onClick={() => setSelected(c.id)}
          >
            {c.label}
          </Button>
        ))}
      </div>
      {current && (
        <pre className="mt-3 max-h-64 overflow-auto rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-2.5 font-mono text-[12px] text-ink-2">
          {current.content}
        </pre>
      )}
    </Modal>
  )
}
