/** A labelled checkbox with a line of explanation under it.
 *
 * Lived inside Agents.tsx until Rooms needed the same control for work hours.
 * The hint is a required prop rather than an optional one: every switch in this
 * app turns something off that a user would otherwise expect to happen, and one
 * without an explanation is a switch people leave alone because they cannot tell
 * what it costs them.
 */
export function Switch({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  hint?: string
}) {
  return (
    <label className="flex max-w-[240px] cursor-pointer items-start gap-2">
      <input
        type="checkbox"
        className="mt-[3px]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0">
        <span className="block text-[12.5px] text-ink">{label}</span>
        {hint && <span className="block text-[11px] leading-snug text-ink-3">{hint}</span>}
      </span>
    </label>
  )
}
