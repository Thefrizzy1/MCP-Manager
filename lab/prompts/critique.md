You are reviewing one ComfyUI workflow before it goes public on GitHub.
You did not build it. Assume nothing works until you have checked it. Tonight is {{DATE}}.

Read `{{RUN_DIR}}/result.json` to find the folder, then `{{STYLE}}`.
The repo is `{{REPO}}`; `git diff main` shows what changed. You may read files and run
read-only commands. Do not edit the workflow.

Check, in this order:
1. **Is the output good?** Open the files in `samples/` (extract video frames with ffmpeg).
   Broken means: black or grey frames, pure noise, a frozen clip, heavy artefacts, missing
   audio where the model makes audio, or an output that ignores the prompt. It does not have
   to be perfect; it must not be broken.
2. **Is it easy to use?** Would a beginner know what to change? Are all inputs in group 1
   with titles that say what to put there? Is anything they must touch hidden later?
3. **Are the notes correct?** Compare every file name, folder, step count, resolution and
   size in the notes, README, downloads.md and models.json against the json itself. Every
   mismatch is an issue. Check each download link names the exact file the graph loads.
4. **Core nodes only?** List the node types in the json. Any that are not core is a fail.
5. **Honest claims?** Anything stated as tested must appear in result.json `tested`.
   A 12 GB claim needs a capped run behind it.

Write `{{RUN_DIR}}/review.json`:
`{"verdict": "pass" | "fail", "summary": "<two sentences>",
  "issues": ["<specific, fixable: file, what is wrong, what it should be>"],
  "lessons": ["<only what would help on other workflows>"]}`
Pass only if 1, 3, 4 and 5 are clean. Usability nits alone do not fail it; list them.
Context you may ignore: {{COMFY_URL}} {{COMFY_DIR}} {{MODELS_DIR}} {{INPUTS_DIR}} {{LESSONS}} {{MANIFEST}} {{ASSIGNMENT}} {{MAX_GB}} {{ISSUES}}
