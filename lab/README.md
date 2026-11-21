# Nightly model lab

Every night the GPU server builds or re-checks one ComfyUI workflow in the house
style, tests it with the same standard inputs, has a second fresh session
critique it, opens a draft pull request on the workflows repo, and deletes the
models it downloaded.

**Status: experimental.** The runner's guards are unit-tested; a full night has not been run yet. See "Before the first night".

## One night
1. Merge any pull requests approved in Plutus → Permissions.
2. Preflight: free disk, tools, inputs, Plutus reachable. Not ready = no run, and the reason is logged to the job board.
3. Start the lab's own ComfyUI under the GPU lock.
4. **Build session** (`prompts/build.md`): pick the model from the Trend Radar (or the queue), research, build, run, look at the output, write the repo folder.
5. **Critique session** (`prompts/critique.md`): a fresh session with read-only tools checks output, usability, notes, core-only and honest claims. A fail goes back for one fix round.
6. `scripts/check_repo.py` must pass.
7. Draft PR, plus an approval request in Plutus. Merged at once only if "Publish workflows" is set to Allowed.
8. Delete what was downloaded tonight, stop ComfyUI, log one line to the job board.

## What keeps it safe
- Runs as its own unix user that can write only under `/srv/lab`.
- Its own ComfyUI and its own models folder. Production ComfyUI and `/srv/models` are never written.
- Deleting is limited to files inside the lab's models folder that were not there when the run started.
- The GitHub token is in the runner's environment only; the sessions run without it, so only the runner can push or merge.
- Merge is decided by Plutus permissions, which only the dashboard can change.

## Before the first night
1. **Disk.** The lab needs about 60 GB free where its models folder lives.
2. **GPU window.** The lab holds the GPU lock while it runs (up to ~3 h). Set `gpu_free_check` to a command that succeeds only when the GPU is free (for a flock-based lock: `["flock", "-n", "/path/to/gpu.lock", "true"]`); a busy GPU then skips the night instead of queueing behind other work.
3. **Standard inputs.** Fill `/srv/lab/inputs` with the files in `inputs/manifest.json`.
4. Create the `lab` user, `/srv/lab/{models,inputs,state}`, clone ComfyUI to `/srv/lab/ComfyUI` with its own venv, and add an `extra_model_paths.yaml` pointing at `/srv/lab/models`.
5. Clone this repo to `/srv/lab/plutus` and your workflows repo to `/srv/lab/workflows`.
6. `claude` logged in for the `lab` user (subscription).
7. A fine-grained GitHub token limited to the workflows repo (contents + pull requests) in `/etc/model-lab.env` as `LAB_GH_TOKEN`.
8. `cp config.example.json config.json`, set `plutus_mcp` and `workflows_remote`, then `python3 lab.py preflight` until every line is ok.
9. Run one night by hand: `python3 lab.py nightly`. Read `/srv/lab/state/runs/<date>/`.
10. Only then enable the timer: copy `systemd/*` to `/etc/systemd/system/` and `systemctl enable --now model-lab.timer`.

## Steering it
- **Queue:** `/srv/lab/state/queue.json`, a list like `[{"task": "build", "model": "LTX 2.5 IC-LoRA"}]`. Empty = it chooses from the Trend Radar, or re-checks the oldest workflow.
- **Lessons:** `/srv/lab/state/LESSONS.md`. Both sessions add to it; write your own corrections there too. It is read at the start of every build.
- **Style:** `STYLE.md`. Change it and the next night follows it.
