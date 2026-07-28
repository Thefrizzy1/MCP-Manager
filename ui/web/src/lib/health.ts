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

export const HEALTH_META: Record<HealthState, { label: string; tone: Tone; desc: string }> = {
  online: { label: 'Online', tone: 'ok', desc: 'Reachable and healthy (HTTP 2xx/3xx, or a local/public tool with its config set).' },
  offline: { label: 'Offline', tone: 'danger', desc: 'Not reachable — connection refused, timeout, DNS failure, or a wrong probe path (404).' },
  auth_error: { label: 'Auth error', tone: 'warn', desc: 'Reachable but rejected the credentials (HTTP 401/403) — check the API key/token.' },
  api_error: { label: 'API error', tone: 'warn', desc: 'Reachable but the service returned a server error (HTTP 5xx).' },
  rate_limited: { label: 'Rate limited', tone: 'warn', desc: 'Reachable but throttling requests right now (HTTP 429).' },
  disabled: { label: 'Disabled', tone: 'muted', desc: 'Ignored — hidden from stats and excluded from the MCP surface until restored.' },
  unconfigured: { label: 'Not configured', tone: 'muted', desc: 'Required URL/credentials are missing — add them under Configure.' },
  unknown: { label: 'Unknown', tone: 'muted', desc: 'Not probed yet, or the result could not be classified. Run Test.' },
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
  if (s.ignored) return 'disabled'
  if (s.health_state) return s.health_state
  if (!s.configured) return 'unconfigured'
  if (s.health === false) return 'offline'
  if (s.health === true) return 'online'
  return 'unknown'
}
