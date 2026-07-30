import { useRoute } from '@/lib/router'
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

export function App() {
  const route = useRoute()
  return <Shell route={route}>{renderPage(route)}</Shell>
}
