import { useRoute } from '@/lib/router'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Shell } from '@/components/Shell'
import { Dashboard } from '@/pages/Dashboard'
import { Connections } from '@/pages/Connections'
import { Discover } from '@/pages/Discover'
import { Trends } from '@/pages/Trends'
import { Jobs } from '@/pages/Jobs'
import { Overview } from '@/pages/Overview'
import { Models } from '@/pages/Models'
import { Permissions } from '@/pages/Permissions'
import { Agents } from '@/pages/Agents'
import { Files } from '@/pages/Files'
import { Settings } from '@/pages/Settings'
import { Placeholder } from '@/pages/Placeholder'

function renderPage(route: string) {
  switch (route) {
    case 'dashboard':
      return <Dashboard />
    case 'connections':
      return <Connections />
    case 'discover':
      return <Discover />
    case 'trends':
      return <Trends />
    case 'overview':
      return <Overview />
    case 'jobs':
      return <Jobs />
    case 'models':
      return <Models />
    case 'permissions':
      return <Permissions />
    case 'agents':
      return <Agents />
    case 'files':
      return <Files />
    case 'settings':
      return <Settings />
    default:
      return <Placeholder route={route} />
  }
}

const TITLES: Record<string, string> = {
  dashboard: 'The dashboard',
  connections: 'Connections',
  discover: 'Discover',
  trends: 'Trends',
  overview: 'Overview',
  jobs: 'YouTube Studio',
  models: 'Workflow Maker',
  permissions: 'Permissions',
  agents: 'Runs',
  files: 'Files',
  settings: 'Settings',
}

export function App() {
  const route = useRoute()
  return (
    <Shell route={route}>
      {/* Keyed by route: the boundary resets when you navigate away, so a page
          that threw once does not latch and break every page after it. */}
      <ErrorBoundary key={route} label={TITLES[route]}>
        {renderPage(route)}
      </ErrorBoundary>
    </Shell>
  )
}
