import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'

interface Profile {
  name: string
  label?: string
  intent?: string
  endpoint_path: string
  tool_count: number
}
interface ProfilesResp {
  profiles: Profile[]
  total_tools: number
}

export function ProfilesSection() {
  const toast = useToast()
  const { data, refetch } = useQuery({ queryKey: ['profiles'], queryFn: () => api.get<ProfilesResp>('/api/v1/profiles') })
  const [name, setName] = useState('')
  const [intent, setIntent] = useState('')
  const [preview, setPreview] = useState<number | null>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (!intent.trim()) {
      setPreview(null)
      return
    }
    let cancelled = false
    const t = setTimeout(async () => {
      try {
        const d = await api.get<{ tool_count: number }>('/api/v1/profiles/preview?intent=' + encodeURIComponent(intent))
        if (!cancelled) setPreview(d.tool_count)
      } catch {
        /* ignore */
      }
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [intent])

  async function create() {
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-')
    if (!slug) {
      setMsg('Name required.')
      return
    }
    try {
      await api.post('/api/v1/profiles', { name: slug, label: name.trim(), intent: intent.trim() })
      setName('')
      setIntent('')
      setMsg('Created — restart the MCP server to serve it.')
      refetch()
    } catch (e) {
      setMsg(String(e))
    }
  }

  async function del(n: string) {
    if (!confirm(`Delete profile ${n}?`)) return
    try {
      await api.del(`/api/v1/profiles/${n}`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  const profiles = data?.profiles ?? []

  return (
    <Card>
      <CardHeader
        title="MCP profiles (advanced)"
        subtitle="Named tool subsets, each served at its own /mcp/p/<name> endpoint — point a focused agent at a smaller manifest."
      />
      <div className="px-4 pb-4">
        {profiles.length > 0 && (
          <div className="mb-3 space-y-1.5">
            {profiles.map((p) => (
              <div key={p.name} className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-border px-3 py-2">
                <span className="font-medium text-ink">{p.label || p.name}</span>
                <code className="font-mono text-[11.5px] text-ink-3">{p.endpoint_path}</code>
                <span className="text-[11.5px] text-ink-3">{p.tool_count} tools</span>
                <Button variant="danger" size="icon-sm" className="ml-auto" onClick={() => del(p.name)} title="Delete">
                  <Trash2 size={13} />
                </Button>
              </div>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Name" className="w-40">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="research" />
          </Field>
          <Field label="Intent (tools to include)" className="min-w-56 flex-1">
            <Input value={intent} onChange={(e) => setIntent(e.target.value)} placeholder="calendar tasks files  ·  or a preset: web / homelab / all" />
          </Field>
          <Button variant="default" size="md" onClick={create}>
            Create profile
          </Button>
        </div>
        <div className="mt-1.5 text-[12px] text-ink-3">
          {preview !== null ? `~${preview} tools would be exposed.` : msg || 'Blank intent = all tools. Restart-to-apply.'}
        </div>
      </div>
    </Card>
  )
}
