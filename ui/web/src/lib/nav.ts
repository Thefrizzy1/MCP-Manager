import {
  LayoutDashboard,
  KanbanSquare,
  ScrollText,
  ShieldCheck,
  Plug,
  Radar,
  Satellite,
  FolderOpen,
  Settings,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Workspace',
    items: [
      { id: 'jobs', label: 'Jobs', icon: KanbanSquare },
      { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
      { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { id: 'connections', label: 'Connections', icon: Plug },
      { id: 'discover', label: 'Discover', icon: Radar },
      { id: 'trends', label: 'Trends', icon: Satellite },
    ],
  },
  {
    label: 'System',
    items: [
      { id: 'files', label: 'Files', icon: FolderOpen },
      // Transcripts of scheduled runs (Idea Studio, nightly jobs). The agent
      // builder and rooms are not part of the studio edition.
      { id: 'agents', label: 'Runs', icon: ScrollText },
    ],
  },
]

export const SETTINGS_ITEM: NavItem = { id: 'settings', label: 'Settings', icon: Settings }

export const ALL_ROUTES = [
  ...NAV_GROUPS.flatMap((g) => g.items.map((i) => i.id)),
  SETTINGS_ITEM.id,
]
