# Plutus — Design system

The v6 web UI (`ui/web/`) is a **professional desktop-app** interface: calm, minimal,
dense, and cohesive. This doc is the reference that keeps future UI changes from
drifting. Reference points: Linear, Raycast, Claude Desktop, GitHub Desktop.

## Principles

- **One cohesive system.** Every screen uses the same tokens, spacing, and components.
  Build reusable components (`ui/web/src/components/ui/`), not one-offs.
- **Dense, not spacious.** Base type is **13px**. That density is what separates this
  from a generic template.
- **Hairline borders, no shadows** except things that float (modals, drawers, toasts,
  popovers). Panels get `1px solid var(--border)` and nothing else.
- **No gradients. No emoji.** Icons are [lucide](https://lucide.dev) (16px). Service
  marks are real brand logos (offline `simple-icons`) with a coloured initial-tile
  fallback.
- **Status colour is semantic, never decorative** — green/amber/red only mean health.
- **Loading is a static muted row.** No skeletons, no shimmer. Respect
  `prefers-reduced-motion`.
- **One filled primary button per view.** Everything else is ghost or default.

## Tokens

Defined in `ui/web/src/index.css` as runtime CSS variables (so the theme switches live),
exposed to Tailwind v4 via `@theme inline`. Light + dark; the theme is set before first
paint to avoid a flash.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f6f7f9` | `#0c0d10` | page |
| `--surface` | `#ffffff` | `#16181d` | cards, sidebar, table, modal |
| `--surface-2` | `#fafbfc` | `#1b1e24` | table header, subtle fills |
| `--surface-hover` | `#f2f4f7` | `#212530` | row/nav hover |
| `--border` | `#e6e8ec` | `#262a31` | every hairline |
| `--border-strong` | `#d4d7de` | `#343943` | inputs, ghost-button borders |
| `--accent` | `#3a5ce5` | `#6e8bff` | one primary/view, links, active nav |
| `--accent-weak` | `#eef2fe` | `#1a2036` | active-nav fill |
| `--ink` / `--ink-2` / `--ink-3` | `#1f2430`/`#5b6472`/`#8a93a3` | (dark inverts) | primary / secondary / muted text |
| `--ok` / `--warn` / `--danger` | `#2e9e5b`/`#e08a2e`/`#d6453c` | (github-dark set) | health, warnings, errors |

Radii: `--radius-sm 4px` (buttons/inputs/nav), `--radius 6px` (panels/cards),
`--radius-lg 10px` (modals). Font: Inter / system stack; mono: JetBrains Mono / ui-mono.

## Type scale

```
19px/600  page title (PageHead)
15px/600  card / section heading, modal title
13px/400  BASE — body, nav, buttons, table cells, labels
12.5px    secondary text, descriptions
11.5px/600 table column headers (uppercase, tracked)
11px      tags, badges, meta
mono 11.5–12.5px  endpoints, addresses, tool names, counts
```

## Component inventory

- **Shell / Sidebar** — 224px sidebar (grouped nav, theme toggle); collapses to a
  drawer under 768px behind a hamburger.
- **PageHead / PageBody** — sticky title + actions row; scrollable, max-width body.
- **ui/**: `Button` (cva variants: primary/default/ghost/danger; sizes sm/md/icon),
  `Card` + `CardHeader`, `Stat`, `StatusDot` / `HealthBadge`, `Tag`, `Input` /
  `Textarea` / `Select`, `Field` / `Row`, `Modal`, `Drawer`, `Toast` (bottom-right,
  replaces native alert), `ServiceLogo`.
- **Health states** (`lib/health.ts`): Online / Offline / Auth error / Rate limited /
  API error / Not configured / Disabled — each a tone (ok/warn/danger/muted).

## Anti-patterns (a change producing these is wrong)

Gradients · drop shadows on cards · radius > 10px · 16px base text · more than one
filled primary button per view · emoji in the UI · teal/neon accents · animated page
transitions / count-ups / skeletons · icon-only buttons without `aria-label`/`title` ·
KPI card grids on every page (only the Dashboard gets stat cards).
