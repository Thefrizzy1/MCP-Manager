export interface ScoreParts {
  stage?: number
  momentum?: number
  buzz?: number
  excitement?: number
  demand?: number
  fit?: number
  saturation?: number
  lowvram_saturation?: number
  creator_videos_10d?: number
  lowvram_videos_10d?: number
  threads_7d?: number
  release_ts?: number | null
  best_views_per_day?: number
  sources_confirming?: number
  confirm_bonus?: number
  idea_catches?: number
  catch_bonus?: number
}

export interface Candidate {
  slug: string
  name: string
  family: string
  first_ts: number
  status: 'watch' | 'pinned' | 'dismissed' | 'covered'
  notes: string
  hot: number
  stage: string
  action: string
  score: ScoreParts
  signal_count?: number
  alerted: number
  updated: number
}

export interface Signal {
  id: number
  source: string
  ext_id: string
  kind: string
  title: string
  url: string
  author: string
  ts: number
  metrics: Record<string, unknown>
  candidate: string
}

export interface Idea {
  id: number
  candidate: string
  title: string
  alt_titles: string[]
  angle: string
  hook: string
  thumb_text: string
  why_now: string
  status: 'new' | 'shortlisted' | 'producing' | 'published' | 'archived'
  archive_reason: string
  catches: number
  hot: number
  created: number
  updated: number
  has_thumb?: number
  description?: string
  script?: string
  thumb_svg?: string
  thumb_notes?: string
  research?: string
  evidence?: string[]
}

export interface Run {
  id: number
  started: number
  finished: number
  ok: number
  detail: {
    sources?: Record<string, { ok: boolean; seen?: number; matched?: number; secs?: number; error?: string }>
    alerts?: string[]
    benchmark?: string
  }
}

export interface SchedInfo {
  id: string
  cron: string
  enabled: boolean
  next?: string | null
  last_run?: string
  last_status?: string
  last_detail?: string
}

export interface Job {
  running: boolean
  started: number
  finished: number | null
  result: unknown
  error: string | null
}

export interface Overview {
  candidates: Candidate[]
  ideas: Idea[]
  runs: Run[]
  schedules: { collect: SchedInfo | null; studio: SchedInfo | null }
  jobs: Record<string, Job>
  stats: { tracked: number; prep: number; shoot: number; ideas: number; threshold: number }
}

export interface Backtest {
  ok: boolean
  error?: string
  threshold: number
  first_signal: number
  alert: number | null
  actionable: number | null
  release: number | null
  first_competitor_video: number | null
  own_upload: number | null
  lead_alert_to_release_h: number | null
  lead_alert_to_first_video_h: number | null
  own_after_release_h: number | null
  own_rank: number | null
  caught_before_release: boolean
  series?: { t: number; hot: number; stage: string }[]
}

export interface Benchmark {
  id: number
  slug: string
  template: string
  started: number
  finished: number
  status: string
  seconds: number | null
  vram_peak: number | null
  detail: string
}

export interface CandidateDetail {
  candidate: Candidate
  signals: Signal[]
  ideas: Idea[]
  benchmarks: Benchmark[]
  backtest: Backtest | null
  templates: string[]
}

export const STAGES = ['rumor', 'paper', 'repo', 'code', 'weights', 'gguf'] as const

export function ago(ts?: number | null): string {
  if (!ts) return '–'
  const d = (Date.now() / 1000 - ts) / 86400
  if (d < 0) return 'soon'
  if (d < 1 / 24) return `${Math.max(1, Math.round(d * 1440))}m ago`
  if (d < 1) return `${Math.round(d * 24)}h ago`
  if (d < 60) return `${Math.round(d)}d ago`
  return `${Math.round(d / 30)}mo ago`
}

export function date(ts?: number | null): string {
  if (!ts) return '–'
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: '2-digit',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function hours(h?: number | null): string {
  if (h === null || h === undefined) return '–'
  const sign = h < 0 ? '−' : '+'
  const a = Math.abs(h)
  return a >= 48 ? `${sign}${(a / 24).toFixed(1)}d` : `${sign}${a.toFixed(0)}h`
}
