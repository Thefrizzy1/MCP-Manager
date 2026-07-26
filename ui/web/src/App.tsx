import { useRoute } from '@/lib/router'
import { Shell } from '@/components/Shell'
import { Dashboard } from '@/pages/Dashboard'
import { Placeholder } from '@/pages/Placeholder'

function renderPage(route: string) {
  switch (route) {
    case 'dashboard':
      return <Dashboard />
    default:
      return <Placeholder route={route} />
  }
}

export function App() {
  const route = useRoute()
  return <Shell route={route}>{renderPage(route)}</Shell>
}
