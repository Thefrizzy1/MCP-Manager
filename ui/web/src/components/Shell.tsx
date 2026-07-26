import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'

export function Shell({ route, children }: { route: string; children: ReactNode }) {
  return (
    <div className="flex h-full">
      <Sidebar route={route} />
      <main className="flex min-w-0 flex-1 flex-col">{children}</main>
    </div>
  )
}
