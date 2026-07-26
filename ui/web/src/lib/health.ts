/** Connection health, per the v10 brief. The backend currently reports a subset
 *  (online/offline/disabled); the richer states are wired for when it enriches. */
export type HealthState =
  | 'online'
  | 'offline'
  | 'auth_error'
  | 'api_error'
  | 'rate_limited'
  | 'disabled'
  | 'unconfigured'
  | 'unknown'

export type Tone = 'ok' | 'warn' | 'danger' | 'muted'

export const HEALTH_META: Record<HealthState, { label: string; tone: Tone }> = {
  online: { label: 'Online', tone: 'ok' },
  offline: { label: 'Offline', tone: 'danger' },
  auth_error: { label: 'Auth error', tone: 'warn' },
  api_error: { label: 'API error', tone: 'warn' },
  rate_limited: { label: 'Rate limited', tone: 'warn' },
  disabled: { label: 'Disabled', tone: 'muted' },
  unconfigured: { label: 'Not configured', tone: 'muted' },
  unknown: { label: 'Unknown', tone: 'muted' },
}

export interface Service {
  id: string
  label: string
  section?: string
  tag?: string
  icon?: string
  url?: string
  configured?: boolean
  ignored?: boolean
  health?: boolean | null
  tool_count?: number
  tool_names?: string[]
  logo_domain?: string
  /** Optional richer state once the backend supplies it. */
  health_state?: HealthState
}

/** Map the current dashboard payload onto a health state. */
export function serviceHealth(s: Service): HealthState {
  if (s.health_state) return s.health_state
  if (s.ignored) return 'disabled'
  if (!s.configured) return 'unconfigured'
  if (s.health === false) return 'offline'
  if (s.health === true) return 'online'
  return 'unknown'
}
