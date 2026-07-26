import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { navigate } from '@/lib/router'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Textarea } from '@/components/ui/Input'
import { Field } from '@/components/ui/Field'

export function Builder() {
  const [desc, setDesc] = useState('')
  const [msg, setMsg] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [draft, setDraft] = useState<{ name: string; prompt: string } | null>(null)

  async function generate() {
    if (desc.trim().length < 5) {
      setMsg('Describe what it should do.')
      return
    }
    setDrafting(true)
    setMsg('Claude is drafting… (up to ~1 min)')
    try {
      const d = await api.post<{ ok?: boolean; error?: string; prompt?: string }>('/api/v1/agent/tasks/build', {
        description: desc.trim(),
      })
      if (!d.ok) {
        setMsg(d.error || 'failed — connect your Claude account in Settings')
      } else {
        setMsg('Drafted — review and launch.')
        setDraft({ name: desc.trim().slice(0, 50), prompt: d.prompt || '' })
      }
    } catch (e) {
      setMsg(String(e))
    } finally {
      setDrafting(false)
    }
  }

  async function launch() {
    if (!draft?.prompt.trim()) return
    try {
      await api.post('/api/v1/agent/run', { prompt: draft.prompt.trim(), label: draft.name || 'agent' })
      navigate('agents')
    } catch (e) {
      alert(String(e))
    }
  }

  return (
    <>
      <PageHead title="AI Builder" subtitle="Describe an agent in plain language — Claude drafts it" />
      <PageBody>
        <div className="space-y-5">
          <Card>
            <CardHeader title="Describe your agent" action={<Sparkles size={16} className="text-accent" />} />
            <div className="px-4 pb-4">
              <p className="mb-2 text-[12.5px] text-ink-3">
                e.g. “Every morning research new low-VRAM ComfyUI workflows and log the best ones to my notes with source
                links.”
              </p>
              <Textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} />
              <div className="mt-3 flex items-center gap-3">
                <Button variant="primary" size="sm" disabled={drafting} onClick={generate}>
                  <Sparkles size={14} /> Draft with Claude
                </Button>
                {msg && <span className="text-[12px] text-ink-3">{msg}</span>}
              </div>
            </div>
          </Card>

          {draft && (
            <Card>
              <CardHeader title="Review & launch" />
              <div className="space-y-3 px-4 pb-4">
                <Field label="Name">
                  <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </Field>
                <Field label="Goal / prompt">
                  <Textarea rows={8} value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} />
                </Field>
                <Button variant="primary" size="sm" onClick={launch}>
                  Create & run now
                </Button>
              </div>
            </Card>
          )}
        </div>
      </PageBody>
    </>
  )
}
