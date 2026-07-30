import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface ProviderModel {
  id: string
  label: string
}

export interface ProviderAccount {
  id: string
  label: string
  authenticated: boolean
  state: string
  config_dir: string
  login_command: string
  role_label: string
  kind: string
  isolated: boolean
  adoptable: boolean
  adoptable_from: string
  accepts_key: boolean
  key_hint: string
  auth_kind: string
}

export interface Provider {
  id: string
  label: string
  kind: 'cli' | 'api'
  runnable: boolean
  state: string
  login_command: string
  cli: { installed: boolean; path: string; version: string; install_hint: string }
  accounts: ProviderAccount[]
  role_label: string
  isolated: boolean
  models: ProviderModel[]
  default_model: string
  accepts_key: boolean
  key_hint: string
}

export interface LinkedAccount {
  provider: string
  providerLabel: string
  role: string
  kind: string
  id: string
  label: string
}

export function useProviders() {
  return useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => api.get<{ providers: Provider[]; guided_login_available: boolean }>('/api/v1/providers'),
    // A CLI installed or a login completed from a terminal has to show up without
    // a page reload — the settings card used to sit on cached data saying "not
    // installed" while Test reported the very same CLI as present.
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
  })
}

/** Only accounts with a completed login (or a stored key) can run anything, so
 *  unlinked ones are never offered as a place to send work. */
export function linkedAccounts(providers: Provider[] | undefined): LinkedAccount[] {
  return (providers ?? [])
    .filter((p) => p.runnable)
    .flatMap((p) =>
      p.accounts
        .filter((a) => a.authenticated)
        .map((a) => ({
          provider: p.id,
          providerLabel: p.label,
          role: p.role_label,
          kind: p.kind,
          id: a.id,
          label: a.label,
        })),
    )
}

/** The models one provider offers — live from the vendor where that is possible.
 *
 *  Keyed by account as well as provider: an HTTP provider's list is fetched with
 *  that account's own key, so two accounts on different tiers legitimately see
 *  different menus. */
export function useProviderModels(provider: string, accountId: string) {
  return useQuery({
    queryKey: ['provider-models', provider, accountId],
    queryFn: () =>
      api.get<{ models: ProviderModel[]; source: string; allow_custom: boolean }>(
        `/api/v1/providers/${encodeURIComponent(provider)}/models` +
          (accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''),
      ),
    enabled: Boolean(provider),
    staleTime: 60_000,
  })
}

/** "claude/personal-ab12" ⇄ its parts. An empty selection means the legacy
 *  single login, which is Claude. */
export function splitAccount(value: string): { provider: string; accountId: string } {
  const [provider = '', accountId = ''] = value ? value.split('/') : []
  return { provider: provider || 'claude', accountId }
}
