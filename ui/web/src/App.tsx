import { useRoute } from '@/lib/router'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Shell } from '@/components/Shell'
import { Dashboard } from '@/pages/Dashboard'
import { Connections } from '@/pages/Connections'
import { Discover } from '@/pages/Discover'
import { Agents } from '@/pages/Agents'
import { Rooms } from '@/pages/Rooms'
import { Builder } from '@/pages/Builder'
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
    case 'agents':
      return <Agents />
    case 'rooms':
      return <Rooms />
    case 'builder':
      return <Builder />
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
  agents: 'Agents',
  rooms: 'Rooms',
  builder: 'The builder',
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
