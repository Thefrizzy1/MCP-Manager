import { AlertTriangle } from 'lucide-react'
import { useWhoami } from '@/lib/auth'
import { navigate } from '@/lib/router'

/** A persistent, non-dismissible nag shown while any account is still on the
 *  seeded admin/adminadmin default. Goes away the moment the password is
 *  changed (the store clears the flag). The pulsing dot is the one bit of
 *  attention-motion here — reduced-motion neutralises it via the global rule. */
export function DefaultPasswordBanner() {
  const who = useWhoami()
  if (!who.data?.default_password_active) return null
  return (
    <div className="flex items-center gap-2.5 border-b border-warn/40 bg-warn-weak px-4 py-2 text-[12.5px] text-ink">
      <span className="relative flex h-2 w-2 shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-warn opacity-70" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-warn" />
      </span>
      <AlertTriangle size={15} className="shrink-0 text-warn" />
      <span className="min-w-0">
        You’re signed in with the <strong>default password</strong>. Change it now —
        anyone on the network can reach this dashboard.
      </span>
      <button
        onClick={() => navigate('settings')}
        className="ml-auto shrink-0 rounded-[var(--radius-sm)] border border-border-strong bg-surface px-2 py-1 text-[12px] font-medium text-ink hover:bg-surface-hover"
      >
        Change password
      </button>
    </div>
  )
}
