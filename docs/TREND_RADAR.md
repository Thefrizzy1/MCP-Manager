# Trend Radar and Idea Studio

The Trends tab answers one question for a "first with the new model" channel:
**what is about to drop, and is there still room for my video?**

## How it works

1. **Collect** (every 3 h by default, schedule "Trend Radar · collect"). Each
   source produces *signals*, stored with the time they happened:

   | Source | Signal | Typical lead before weights |
   |---|---|---|
   | arXiv, HF daily papers | `paper` | 2-8 weeks |
   | Watched HF orgs | `repo` (empty) / `weights` / `gguf` | 1-14 days |
   | Diffusers, ComfyUI, wrapper repos | `code` (pull requests) | 1-10 days |
   | New repos by e.g. kijai | `repo` | release day |
   | Quant uploaders (city96, QuantStack…) | `gguf` | 0-2 days |
   | Reddit, Hacker News | `buzz` with score, comments, excitement | parallel |
   | Competitor channels | `video` / `short` (views) | after |
   | Your channel | `own` | after |

2. **Match.** Titles are matched to a model by a regex over the family list
   (`wan`, `ltx`, `hunyuanvideo`…). New names from watched HF orgs become new
   families automatically. No LLM is involved, so a backtest cannot be
   contaminated by a model that already knows how a launch went.

3. **Score** each model 0-100: stage (code and weights are the sweet spot),
   momentum (last 48 h), buzz and excitement, demand (best competitor views/day,
   HF likes), niche fit (video, ≤14B, GGUF, ComfyUI; closed APIs lower), minus
   saturation. Saturation counts generic coverage at half weight while nobody
   has done a low-VRAM take. Bonuses: several source types confirming, and the
   Idea Studio arriving at the same idea more than once.

4. **Act.** `PREP NOW` (code public, weights not), `SHOOT NOW`, `SHOOT: 4GB
   ANGLE` (covered, but not on weak hardware), `LATE`, `WATCH`, `DONE`.

## Idea Studio

A normal Plutus agent run (schedule "Trend Radar · Idea Studio", off by
default) started in `data/trend_radar_studio/`. Claude Code reads that folder's
`CLAUDE.md` and `.claude/skills/`, so the skills you upload on the Studio tab
(`SKILL.md`, `.zip` or `.skill`) are the ones it uses. It calls
`trend_radar_list`, `trend_radar_candidate` and `trend_radar_title_research`,
then `trend_radar_save_idea`.

- An idea on the same model with an overlapping title/angle **merges**: the new
  title becomes an alternative and `catches` goes up, which raises the hot score.
- Ideas auto-archive (with the reason) when the model is dismissed, the window
  closed, it went cold, or it sat untouched for three weeks. Shortlisted ideas
  never auto-archive.
- **Looks finished**: your own uploads are cross-referenced with open ideas and
  models. Apply one or all; they move to the Finished drawer.
- Thumbnail mocks are SVG, served from their own URL with a no-script CSP and
  shown through `<img>`, so agent-written markup can draw but never run.

## Backtest

Backtest → *Import a past model* pulls dated history (HF, GitHub PR search,
Reddit search, arXiv, YouTube with an API key). The replay calls the same scorer
with an earlier "now" every 6 hours and reports when it would have alerted,
the lead over the weights and the first competitor video, and your rank.

Caveat: Hugging Face `createdAt` is when a repo was created, which for
private-then-public repos is before anyone could see it. Those rows are flagged;
GitHub and Reddit dates are the safer anchors.

## Auto-benchmark

Settings → Auto-benchmark: per family, paste a ComfyUI workflow exported with
*Save (API)* and write `{{seed}}` where the seed goes. Runs go to `COMFYUI_URL`.
Recorded: wall time from ComfyUI's own execution timestamps, peak VRAM (sampled
from `/system_stats`), and the outcome: `ok`, `oom`, `missing_model`, `invalid`,
`error` or `timeout`. The model files must already be on the ComfyUI machine.

## Produce: explainers, VibeVoice, Remotion

*Make explainer* on an idea queues a Claude run (studio folder, your skills) that
saves a production: scenes with a chapter, speaker, spoken narration and a
visual (`title`, `bullets`, `code`, `benchmark`, `comparison`, `image`,
`quote`, `clip`). With a reference video URL it reads that video's shape
(chapter count and length, words per minute, hook length, most-replayed
moments) and matches it; it never gets the transcript to copy.

From the scenes Plutus derives:

- `script.md` in StudioForge's format (`##` chapters, `_(stage directions)_`);
- the VibeVoice script (`[1]: line`);
- a Remotion project in the channel colours (cyan chapters, yellow stats, red
  errors), with scene lengths taken from the real audio.

**Voice.** Runs on `COMFYUI_URL` (the Windows PC). Build the VibeVoice workflow in
ComfyUI, put `{{line}}` (or `{{text}}`) in the text input and optionally
`{{seed}}`, end with SaveAudio (FLAC gives exact lengths), export with
*Save (API)* and paste it under Settings → Voice. One job per scene; re-voicing
one scene keeps the rest.

**Render.** *Render here* uses the Node in the Plutus image
(`npx remotion render`). Remotion downloads its own headless browser on the
first render; a local Chrome/Chromium is used when present
(`REMOTION_BROWSER` to point at one). CPU-only, so expect minutes, not seconds.

**Download / export.** The package zip holds `script.md`, `voice/`,
`benchmarks.md`, `studioforge_import.json`, `titles.md`, the thumbnail mock and
`remotion/`. With an export folder set (a NAS share the PC sees) it is copied
there too.

## Benchmarks

Auto runs (ComfyUI) and manual runs live in one table. Add a run, or paste a
StudioForge notes table (`group | label | file | time | mark`). Pick A and B for
a delta. *StudioForge export* gives the `{"graphics": [...]}` document its
benchmark/comparison comps import. Explainer benchmark scenes reference these
rows by id, so a video only ever shows a measured time.

## Auto mode

Off by default; every step is a button. With it on, the schedule
"Trend Radar · night run" (02:00) wakes the PC (Wake-on-LAN, needs the
container on the host network), then voices, renders and packages productions
whose *Night run* switch is on, up to *max per night*. Optionally it also drafts
explainers for shortlisted ideas.

## MCP tools

`trend_radar_list`, `trend_radar_candidate`, `trend_radar_signals`,
`trend_radar_ideas`, `trend_radar_idea`, `trend_radar_save_idea`,
`trend_radar_set_status`, `trend_radar_done`, `trend_radar_title_research`,
`trend_radar_backfill`, `trend_radar_backtest`, `trend_radar_benchmark`,
`trend_radar_collect`, `trend_radar_production`, `trend_radar_save_production`,
`trend_radar_reference`, `trend_radar_voice`, `trend_radar_render`,
`trend_radar_benchmarks`, `trend_radar_add_benchmark`, `trend_radar_auto_run`.
They are in the `trends` slice of the tool slicer.

## Keys it uses

None of its own. YouTube Data API key (exact views and dates), `GITHUB_TOKEN`
(rate limits), `HF_TOKEN` (rate limits), a Reddit login (scores and comment
counts instead of bare feeds), ntfy (optional alerts), `COMFYUI_URL`
(benchmarks).
