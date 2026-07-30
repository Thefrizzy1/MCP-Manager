import {
  LayoutDashboard,
  Plug,
  Radar,
  Bot,
  Users,
  Sparkles,
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
      { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { id: 'connections', label: 'Connections', icon: Plug },
      { id: 'discover', label: 'Discover', icon: Radar },
    ],
  },
  {
    label: 'Automation',
    items: [
      { id: 'agents', label: 'Agents', icon: Bot },
      { id: 'rooms', label: 'Rooms', icon: Users },
      { id: 'builder', label: 'AI Builder', icon: Sparkles },
    ],
  },
  {
    label: 'Storage',
    items: [{ id: 'files', label: 'Files', icon: FolderOpen }],
  },
]

export const SETTINGS_ITEM: NavItem = { id: 'settings', label: 'Settings', icon: Settings }

export const ALL_ROUTES = [
  ...NAV_GROUPS.flatMap((g) => g.items.map((i) => i.id)),
  SETTINGS_ITEM.id,
]
