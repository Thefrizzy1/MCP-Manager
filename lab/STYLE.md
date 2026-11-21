# House style

The standard every lab-built workflow is held to. This file is an example,
read off a pair of published reference workflows (a multi-group video workflow
and a single-subgraph image workflow): edit it to describe your own. Where this file and the repo's
`docs/NAMING.md` or `templates/WORKFLOW_README_TEMPLATE.md` disagree, the repo wins.

## The edge: core nodes only
- Only nodes that ship with ComfyUI. No custom node packs. Check every node's
  class against `/object_info` of a clean install; `models.json` `"nodes"` stays `[]`.
- If a model has no core support yet, do not build it. Report `waiting_for_core`
  with the PR or issue that will add it.

## Target hardware
- 12 GB VRAM is the published target (RTX 3060). Prefer the Comfy-Org repackaged
  files (`int8_convrot`, fp8, or GGUF where core loads them) over full precision.
- The lab server has 24 GB. A run there proves the graph works, not that it fits
  12 GB. Write "Not verified on 12 GB" unless the run was capped to 12 GB and
  peak VRAM stayed under it.

## Graph layout
- Numbered groups in the order a person uses them, titles in plain words:
  `1 · Your inputs (App mode shows these)`, `2 · Models (loaded once)`,
  `3 · Prompt …`, … last group `Save`. One colour per group.
- Every input a user should touch gets a plain-language title that says what to
  put there and the safe value: `Short side (512 fits 12 GB)`, `Steps (6 = best value)`,
  `Seconds (0 = whole clip)`.
- Inputs are primitives wired into the graph, collected in group 1, so App mode
  shows only them. Nothing a beginner must change is buried in a later group.
- Simple models can be one subgraph node with its inputs on the node (Qwen 2.1 style).
- Optional paths (turbo LoRA, upscaler) are present but bypassed, and named as such.

## Notes inside the workflow (MarkdownNote)
- `How to use · vX.Y.Z`: one sentence on what it does, "Core ComfyUI nodes only",
  then numbered steps matching the group numbers, then what to expect (time, size).
- `Downloads + requirements`: a table `Folder | File (click = download) | Size`,
  folders relative to `ComfyUI/models/`, every file name a direct download link.
- A short "why" note next to anything surprising (a loop, a fixed sigma list).
- Every number and file name in a note must match the graph. This is the most
  common defect: check each one.

## Repository folder
`workflows/<family>/<slug>/` with: `<Author>_<slug>_vX.Y.Z.json`, `README.md`
(from the template, "Not verified" instead of guessing), `changelog.md`,
`downloads.md`, `models.json` (every file the graph loads, exact names),
`samples/` (2 outputs as .webp plus `workflow.png`), `source/` for older versions.
New workflow starts at v1.0.0; a settings fix is a PATCH; a new capability is a MINOR.
Add the row to the root README table. `python scripts/check_repo.py` must pass.

## Voice
Short, direct, no hype. Say what it does and what it costs (VRAM, time). Admit
what was not tested.
