import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Textarea } from '@/components/ui/Input'
import { Row } from '@/components/ui/Field'
import { ClientExportModal } from '@/components/settings/ClientExportModal'
import { ProfilesSection } from '@/components/settings/ProfilesSection'
import { ClaudeCodeSection } from '@/components/settings/ClaudeCodeSection'
import { useToast } from '@/components/ui/Toast'

interface DashAuthNet {
  networking?: { http_local?: string; public_base?: string; mcp_lan_host?: string }
  auth?: { mcp_require_bearer?: boolean; mcp_bearer_configured?: boolean; mcp_oauth_enabled?: boolean }
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      <div className="px-4 pb-3">{children}</div>
    </Card>
  )
}

export function Settings() {
  const toast = useToast()
  const dash = useQuery({
    queryKey: ['dashboard-settings'],
    queryFn: () => api.get<DashAuthNet>('/api/v1/dashboard?sections=auth,networking'),
  })
  const health = useQuery({ queryKey: ['server-health'], queryFn: () => api.get<{ version?: string }>('/server/health') })
  const ci = useQuery({
    queryKey: ['custom-integrations'],
    queryFn: () => api.get<unknown>('/settings/custom-integrations'),
  })

  const [pub, setPub] = useState('')
  const [host, setHost] = useState('')
  const [requireBearer, setRequireBearer] = useState(false)
  const [oauthEnabled, setOauthEnabled] = useState(false)
  const [token, setToken] = useState('')
  const [city, setCity] = useState('')
  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [ciText, setCiText] = useState('')
  const [ciMsg, setCiMsg] = useState('')
  const [exportOpen, setExportOpen] = useState(false)

  useEffect(() => {
    if (dash.data) {
      setPub(dash.data.networking?.public_base ?? '')
      setHost(dash.data.networking?.mcp_lan_host ?? '')
      setRequireBearer(!!dash.data.auth?.mcp_require_bearer)
      setOauthEnabled(!!dash.data.auth?.mcp_oauth_enabled)
    }
  }, [dash.data])
  useEffect(() => {
    if (ci.data) setCiText(JSON.stringify(ci.data, null, 2))
  }, [ci.data])

  async function save(body: Record<string, unknown>, ok = 'Saved.') {
    try {
      await api.post('/env/save', body)
      toast.success(ok)
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function generateToken() {
    try {
      const d = await api.post<{ token?: string }>('/settings/generate-token')
      setToken(d.token || '')
      toast.success('Token generated — copy it now.')
    } catch (e) {
      toast.error(String(e))
    }
  }
  async function saveCi() {
    let parsed: unknown
    try {
      parsed = JSON.parse(ciText)
    } catch {
      setCiMsg('Invalid JSON')
      return
    }
    try {
      await api.post('/settings/custom-integrations', parsed)
      setCiMsg('Saved — reload to see cards.')
    } catch (e) {
      setCiMsg(String(e))
    }
  }
  async function reset(scope: string) {
    if (!confirm(`Reset ${scope}?`)) return
    try {
      await api.post('/api/v1/settings/reset', { scopes: [scope] })
      toast.success('Reset done.')
      dash.refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  const n = dash.data?.networking
  const bearerConfigured = dash.data?.auth?.mcp_bearer_configured

  return (
    <>
      <PageHead
        title="Settings"
        subtitle="Configure Plutus"
      />
      <PageBody>
        <div className="space-y-5">
          <Section title="MCP endpoint" subtitle="How clients reach the MCP server">
            <Row label="LAN URL">
              <code className="font-mono text-[12px] text-ink-2">{n?.http_local || '—'}</code>
            </Row>
            <Row label="Public HTTPS base">
              <Input value={pub} onChange={(e) => setPub(e.target.value)} placeholder="https://mcp.your-ts.net" />
            </Row>
            <Row label="LAN host">
              <Input value={host} onChange={(e) => setHost(e.target.value)} />
            </Row>
            <Row label="">
              <Button
                variant="primary"
                size="sm"
                onClick={() => save({ PUBLIC_MCP_BASE: pub.trim(), MCP_LAN_HOST: host.trim() }, 'Saved — restart to apply.')}
              >
                Save URLs
              </Button>
              <span className="text-[11.5px] text-ink-3">restart Plutus to apply</span>
            </Row>
            <Row label="Require bearer">
              <input type="checkbox" checked={requireBearer} onChange={(e) => setRequireBearer(e.target.checked)} />
              <Button
                variant="default"
                size="sm"
                onClick={() => save({ MCP_REQUIRE_BEARER: requireBearer }, 'Saved — effective within seconds.')}
              >
                Save
              </Button>
            </Row>
            <Row label="Token" hint={bearerConfigured ? 'configured (hidden)' : 'not set'}>
              {token ? (
                <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink-2">{token}</code>
              ) : (
                <span className="text-[12px] text-ink-3">Generate a new bearer token</span>
              )}
              <Button variant="default" size="sm" onClick={generateToken}>
                Generate
              </Button>
            </Row>
            <Row label="Connect a client">
              <Button variant="default" size="sm" onClick={() => setExportOpen(true)}>
                Export config (Claude, Cursor, …)
              </Button>
            </Row>
            <Row
              label="Browser OAuth"
              hint="for claude.ai custom connectors"
            >
              <input type="checkbox" checked={oauthEnabled} onChange={(e) => setOauthEnabled(e.target.checked)} />
              <Button
                variant="default"
                size="sm"
                onClick={() => save({ MCP_OAUTH_ENABLED: oauthEnabled }, 'Saved — restart the MCP server to apply.')}
              >
                Save
              </Button>
              <span className="text-[11.5px] text-ink-3">needs public HTTPS + Require bearer on</span>
            </Row>
          </Section>

          <ClaudeCodeSection />

          <Section title="Custom integrations" subtitle="Extra service cards (JSON). Or use Add on the Connections page.">
            <Textarea
              rows={8}
              spellCheck={false}
              className="font-mono text-[11.5px]"
              value={ciText}
              onChange={(e) => setCiText(e.target.value)}
            />
            <div className="mt-2 flex items-center gap-3">
              <Button variant="primary" size="sm" onClick={saveCi}>
                Save integrations
              </Button>
              {ciMsg && <span className="text-[12px] text-ink-3">{ciMsg}</span>}
            </div>
          </Section>

          <ProfilesSection />

          <Section title="Defaults">
            <Row label="Weather city">
              <Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Hamburg" />
              <Button variant="default" size="sm" onClick={() => save({ WEATHER_DEFAULT_LOCATION: city.trim() })}>
                Save
              </Button>
            </Row>
            <Row label="UI username">
              <Input value={user} onChange={(e) => setUser(e.target.value)} />
            </Row>
            <Row label="New password" hint="blank = keep current">
              <Input type="password" value={pass} onChange={(e) => setPass(e.target.value)} />
              <Button
                variant="default"
                size="sm"
                onClick={() => {
                  const body: Record<string, string> = {}
                  if (user.trim()) body.UI_USERNAME = user.trim()
                  if (pass) body.UI_PASSWORD = pass
                  if (Object.keys(body).length) save(body, 'Saved — restart to apply.')
                }}
              >
                Save
              </Button>
            </Row>
          </Section>

          <Section title="Maintenance">
            <div className="flex flex-wrap gap-2 py-1">
              <Button variant="default" size="sm" onClick={() => reset('urls')}>
                Reset URLs
              </Button>
              <Button variant="default" size="sm" onClick={() => reset('weather')}>
                Reset weather
              </Button>
              <Button variant="danger" size="sm" onClick={() => reset('custom_integrations')}>
                Clear custom cards
              </Button>
            </div>
          </Section>

          <Section title="About">
            <Row label="Version">
              <span className="text-[12.5px] text-ink-2">Plutus v{health.data?.version ?? '?'}</span>
            </Row>
            <Row label="Bearer status">
              <span className="text-[12.5px] text-ink-2">
                {requireBearer ? (bearerConfigured ? 'Required · token set' : 'Required · NO token set!') : 'Off'}
              </span>
            </Row>
          </Section>
        </div>
      </PageBody>
      {exportOpen && <ClientExportModal onClose={() => setExportOpen(false)} />}
    </>
  )
}
