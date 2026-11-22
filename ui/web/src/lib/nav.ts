import {
  LayoutDashboard,
  Clapperboard,
  MessageSquare,
  Video,
  LayoutGrid,
  Boxes,
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
    // Machines, workers and the calendar at a glance.
    label: 'Studio',
    items: [{ id: 'overview', label: 'Overview', icon: LayoutGrid }],
  },
  {
    // Everything about making and publishing videos.
    label: 'YouTube Studio',
    items: [
      { id: 'jobs', label: 'Videos', icon: Clapperboard },
      { id: 'chat', label: 'Chat', icon: MessageSquare },
      { id: 'film', label: 'Film', icon: Video },
    ],
  },
  {
    // New models in, tested ComfyUI workflows out.
    label: 'Workflow Maker',
    items: [{ id: 'models', label: 'Models', icon: Boxes }],
  },
  {
    label: 'MCP connections',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { id: 'connections', label: 'Connections', icon: Plug },
      { id: 'discover', label: 'Discover', icon: Radar },
    ],
  },
  {
    label: 'System',
    items: [
      { id: 'permissions', label: 'Permissions', icon: ShieldCheck },
      { id: 'files', label: 'Files', icon: FolderOpen },
      // Transcripts of scheduled runs (Idea Studio, nightly jobs).
      { id: 'agents', label: 'Runs', icon: ScrollText },
      // The full radar: signals, scoring, Idea Studio. Models is the short view.
      { id: 'trends', label: 'Trend Radar', icon: Satellite },
    ],
  },
]

export const SETTINGS_ITEM: NavItem = { id: 'settings', label: 'Settings', icon: Settings }

export const ALL_ROUTES = [
  ...NAV_GROUPS.flatMap((g) => g.items.map((i) => i.id)),
  SETTINGS_ITEM.id,
]
