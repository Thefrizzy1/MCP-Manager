import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Input'
import { useToast } from '@/components/ui/Toast'

interface AgentStatus {
  auth?: { mode?: string }
  claude_available?: boolean
}

export function ClaudeCodeSection() {
  const qc = useQueryClient()
  const toast = useToast()
  const { data } = useQuery({
    queryKey: ['agent-status'],
    queryFn: () => api.get<AgentStatus>('/api/v1/agent/status'),
    retry: 0,
  })
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)

  const mode = data?.auth?.mode ?? 'none'
  const connected = mode === 'session_token' || mode === 'subscription'
  const tone = connected ? 'ok' : mode === 'api_key' ? 'warn' : 'danger'
  const label =
    mode === 'session_token'
      ? 'Connected — session token (your Claude plan)'
      : mode === 'subscription'
        ? 'Connected — Claude Code login (your plan)'
        : mode === 'api_key'
          ? 'Using ANTHROPIC_API_KEY (pay-per-token API billing)'
          : 'Not connected — agents can’t run yet'

  async function connect() {
    if (!token.trim()) return
    setSaving(true)
    try {
      const r = await api.post<{ ok?: boolean; error?: string }>('/api/v1/agent/login/token', { token: token.trim() })
      if (r.ok) {
        toast.success('Connected — agents will use your Claude plan.')
        setToken('')
        qc.invalidateQueries({ queryKey: ['agent-status'] })
      } else {
        toast.error(r.error || 'Failed to save token.')
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader
        title="Agent — Claude Code"
        subtitle="How headless agents sign in. Uses your subscription via a session token — never an API key."
      />
      <div className="space-y-3 px-4 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn('inline-block h-2 w-2 rounded-full', tone === 'ok' ? 'bg-ok' : tone === 'warn' ? 'bg-warn' : 'bg-danger')} />
          <span className="text-[13px] text-ink">{label}</span>
          {data?.claude_available === false && (
            <span className="text-[11.5px] text-warn">· claude CLI not found in the container</span>
          )}
        </div>

        <p className="text-[12.5px] text-ink-3">
          On a machine signed into your Claude plan, run{' '}
          <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11.5px] text-ink">claude setup-token</code>{' '}
          and paste the token it prints below. It’s stored as <code className="font-mono text-[11.5px]">CLAUDE_CODE_OAUTH_TOKEN</code> and applied immediately.
        </p>
        <Textarea
          rows={3}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Paste your claude setup-token here…"
          className="font-mono text-[11.5px]"
          spellCheck={false}
        />
        <Button variant="primary" size="sm" disabled={saving || !token.trim()} onClick={connect}>
          {connected ? 'Update token' : 'Connect Claude Code'}
        </Button>
      </div>
    </Card>
  )
}
