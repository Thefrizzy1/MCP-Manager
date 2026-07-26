import { useState } from 'react'
import { Radar, Globe } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Tag } from '@/components/ui/Tag'
import { ServiceLogo } from '@/components/ui/ServiceLogo'
import { ConfigureModal } from '@/components/connections/ConfigureModal'

interface Suggestion {
  service_id?: string
  label?: string
  source?: string
  editable_keys?: Array<{ key?: string; value?: string }>
}
interface ScanResp {
  docker?: { ok?: boolean; containers_seen?: number }
  suggestions?: Suggestion[]
}
interface Op {
  method: string
  path: string
  summary?: string
}
interface Introspect {
  ok: boolean
  error?: string
  title?: string
  version?: string
  operation_count?: number
  spec_url?: string
  description?: string
  base?: string
  operations?: Op[]
}

export function Discover() {
  const [host, setHost] = useState('')
  const [ports, setPorts] = useState(true)
  const [scanMsg, setScanMsg] = useState('')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [scanning, setScanning] = useState(false)

  const [apiUrl, setApiUrl] = useState('')
  const [spec, setSpec] = useState<Introspect | null>(null)
  const [apiMsg, setApiMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [configureId, setConfigureId] = useState<string | null>(null)

  async function scan() {
    if (!host.trim()) return
    setScanning(true)
    setScanMsg('Scanning…')
    setSuggestions([])
    try {
      const d = await api.post<ScanResp>('/api/v1/wizard/scan', { host: host.trim(), include_port_scan: ports })
      setSuggestions(d.suggestions ?? [])
      setScanMsg(
        `${d.docker?.ok ? `${d.docker.containers_seen} containers` : 'Docker unavailable'} · ${(d.suggestions ?? []).length} suggestion(s)`,
      )
    } catch (e) {
      setScanMsg(String(e))
    } finally {
      setScanning(false)
    }
  }

  async function configureAll() {
    const body: Record<string, string> = {}
    for (const it of suggestions) for (const k of it.editable_keys ?? []) if (k.key && k.value) body[k.key] = k.value
    const n = Object.keys(body).length
    if (!n) return
    try {
      await api.post('/env/save', body)
      setScanMsg(`Saved ${n} address(es). Add any API keys on the Connections page.`)
      navigate('connections')
    } catch (e) {
      alert(String(e))
    }
  }

  async function configure(it: Suggestion) {
    // Save the discovered URL(s) first, then open the Configure form right here —
    // pre-filled with the found address:port, so you only add an API key.
    const body: Record<string, string> = {}
    for (const k of it.editable_keys ?? []) if (k.key && k.value) body[k.key] = k.value
    try {
      if (Object.keys(body).length) await api.post('/env/save', body)
      if (it.service_id) setConfigureId(it.service_id)
      else navigate('connections')
    } catch (e) {
      alert(String(e))
    }
  }

  async function introspect() {
    if (!apiUrl.trim()) return
    setBusy(true)
    setApiMsg('Discovering…')
    setSpec(null)
    try {
      const d = await api.post<Introspect>('/api/v1/openapi/introspect', { url: apiUrl.trim() })
      if (!d.ok) {
        setApiMsg(d.error || 'No spec found.')
      } else {
        setSpec(d)
        setApiMsg('')
      }
    } catch (e) {
      setApiMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveApi() {
    if (!spec) return
    const name = (spec.title || 'api').trim()
    const id = (name.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^_+|_+$/g, '').slice(0, 40)) || 'api'
    const urlEnv = id.toUpperCase() + '_URL'
    setBusy(true)
    try {
      const full = await api.get<{ version?: number; integrations?: unknown[] }>('/settings/custom-integrations')
      const list = Array.isArray(full.integrations) ? full.integrations : []
      list.push({
        id,
        label: name,
        description: (spec.description || `OpenAPI service — ${spec.operation_count} endpoints`).slice(0, 200),
        url_env: urlEnv,
        url_placeholder: spec.base || '',
        health_path: '/',
      })
      await api.post('/settings/custom-integrations', { version: full.version || 1, integrations: list })
      if (spec.base) await api.post('/env/save', { [urlEnv]: spec.base })
      setApiMsg('✓ Saved as a connection.')
    } catch (e) {
      setApiMsg(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHead title="Discover" subtitle="Auto-detect services on your network" />
      <PageBody>
        <div className="space-y-5">
          <Card>
            <CardHeader title="Scan a host" action={<Radar size={16} className="text-ink-3" />} />
            <div className="px-4 pb-4">
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="LAN host / IP (e.g. 192.168.1.111)"
                  className="w-64"
                />
                <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
                  <input type="checkbox" checked={ports} onChange={(e) => setPorts(e.target.checked)} /> probe common ports
                </label>
                <Button variant="primary" size="sm" disabled={scanning} onClick={scan}>
                  Scan
                </Button>
              </div>
              {scanMsg && <p className="mt-2 text-[12px] text-ink-3">{scanMsg}</p>}
              {suggestions.length > 1 && (
                <div className="mt-2 flex items-center gap-2">
                  <Button variant="primary" size="sm" onClick={configureAll}>
                    Configure all {suggestions.length}
                  </Button>
                  <span className="text-[11.5px] text-ink-3">saves every found address at once</span>
                </div>
              )}
              <div className="mt-2 space-y-1.5">
                {suggestions.map((it, i) => {
                  const url = it.editable_keys?.[0]?.value || ''
                  return (
                    <div key={i} className="flex items-center gap-2.5 rounded-[var(--radius)] border border-border px-3 py-2">
                      <ServiceLogo id={it.service_id || String(i)} label={it.label} size={22} />
                      <strong className="text-[13px] text-ink">{it.label || it.service_id}</strong>
                      <Tag>{it.source || ''}</Tag>
                      <code className="ml-auto font-mono text-[11.5px] text-ink-3">{url}</code>
                      <Button variant="primary" size="sm" onClick={() => configure(it)}>
                        Configure →
                      </Button>
                    </div>
                  )
                })}
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Discover an API (OpenAPI / FastAPI)"
              subtitle="Point at any service that serves an OpenAPI spec; it reads the endpoints."
              action={<Globe size={16} className="text-ink-3" />}
            />
            <div className="px-4 pb-4">
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  value={apiUrl}
                  onChange={(e) => setApiUrl(e.target.value)}
                  placeholder="http://192.168.1.111:9000"
                  className="w-72"
                />
                <Button variant="primary" size="sm" disabled={busy} onClick={introspect}>
                  Discover
                </Button>
              </div>
              {apiMsg && <p className="mt-2 text-[12px] text-ink-3">{apiMsg}</p>}
              {spec && (
                <div className="mt-3">
                  <div className="mb-2 flex items-center gap-2 text-[12.5px] text-ink-2">
                    <strong className="text-ink">{spec.title}</strong> v{spec.version} · {spec.operation_count} endpoints
                    <Button variant="primary" size="sm" className="ml-auto" disabled={busy} onClick={saveApi}>
                      Add as connection
                    </Button>
                  </div>
                  <div className="max-h-72 overflow-auto rounded-[var(--radius)] border border-border">
                    <table className="w-full text-[12.5px]">
                      <tbody>
                        {(spec.operations ?? []).slice(0, 200).map((o, i) => (
                          <tr key={i} className="border-b border-border last:border-0">
                            <td className="px-3 py-1.5">
                              <Tag>{o.method}</Tag>
                            </td>
                            <td className="px-3 py-1.5 font-mono text-ink">{o.path}</td>
                            <td className="px-3 py-1.5 text-ink-3">{o.summary}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </Card>
        </div>
      </PageBody>
      {configureId && (
        <ConfigureModal
          id={configureId}
          onClose={() => setConfigureId(null)}
          onSaved={() => {
            setConfigureId(null)
            navigate('connections')
          }}
        />
      )}
    </>
  )
}
