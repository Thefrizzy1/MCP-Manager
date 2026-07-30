import { useState } from 'react'
import { Input, Select } from '@/components/ui/Input'
import { useProviderModels } from '@/lib/providers'

const CUSTOM = '__custom__'

/** The models the *selected account's* provider actually offers.
 *
 *  This used to be three hardcoded options — Opus, Sonnet, Haiku — shown no
 *  matter which account you picked, so choosing "run this via Codex" still
 *  offered you Claude models and sent a model id Codex has never heard of.
 *
 *  Every menu keeps a free-text escape hatch: CLIs gain and lose model ids
 *  between releases, and being unable to select a model your account can reach
 *  is worse than an incomplete list.
 */
export function ModelPicker({
  provider,
  accountId,
  value,
  onChange,
  className,
}: {
  provider: string
  accountId: string
  value: string
  onChange: (model: string) => void
  className?: string
}) {
  const q = useProviderModels(provider, accountId)
  const models = q.data?.models ?? []
  const [typing, setTyping] = useState(false)
  // A value that is not in the menu (a past run's model, or one typed earlier)
  // has to keep showing as custom, or reopening the form would silently reset it.
  const unknown = Boolean(value) && models.length > 0 && !models.some((m) => m.id === value)
  const custom = typing || unknown

  return (
    <div className="space-y-1.5">
      <Select
        className={className}
        value={custom ? CUSTOM : value}
        onChange={(e) => {
          if (e.target.value === CUSTOM) {
            setTyping(true)
            return
          }
          setTyping(false)
          onChange(e.target.value)
        }}
      >
        {models.length === 0 && <option value="">Account default</option>}
        {models.map((m) => (
          <option key={m.id || 'default'} value={m.id}>
            {m.label}
          </option>
        ))}
        <option value={CUSTOM}>Other — type a model id…</option>
      </Select>
      {custom && (
        <Input
          autoFocus
          className={className}
          value={value}
          placeholder="e.g. gpt-5.1-codex"
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {q.data?.source === 'live' && (
        <p className="text-[11px] text-ink-3">Listed live from your account.</p>
      )}
    </div>
  )
}
