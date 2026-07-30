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
  const all = q.data?.models ?? []
  const [typing, setTyping] = useState(false)
  const [freeOnly, setFreeOnly] = useState(false)
  // A value that is not in the menu (a past run's model, or one typed earlier)
  // has to keep showing as custom, or reopening the form would silently reset it.
  const unknown = Boolean(value) && all.length > 0 && !all.some((m) => m.id === value)
  const custom = typing || unknown

  // OpenRouter lists several hundred models. "Which of these costs nothing" is
  // the question that actually gets asked, so it gets a switch rather than a
  // scroll — but only where there is a mix worth filtering.
  const hasFree = all.some((m) => m.free)
  const hasPaid = all.some((m) => m.id && !m.free)
  const models = freeOnly ? all.filter((m) => !m.id || m.free) : all

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
      {hasFree && hasPaid && (
        <label className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
          <input
            type="checkbox"
            checked={freeOnly}
            onChange={(e) => setFreeOnly(e.target.checked)}
          />
          Free models only ({all.filter((m) => m.free).length})
        </label>
      )}
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
        <p className="text-[11px] text-ink-3">
          {all.length - 1} models listed live from your account.
        </p>
      )}
    </div>
  )
}
