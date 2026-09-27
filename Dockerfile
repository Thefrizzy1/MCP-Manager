# ── Stage 1: build the React web UI (Vite → ui/static/dist) ───────────────────
# Kept in its own stage so the frontend's node_modules never reach the final
# image — only the built, hashed assets are copied across.
FROM node:22-alpine AS webbuild
WORKDIR /build
# Install deps first so this layer caches unless the lockfile changes.
COPY ui/web/package.json ui/web/package-lock.json ./ui/web/
RUN cd ui/web && npm ci
# Vite's outDir is ../static/dist, so the build lands at /build/ui/static/dist.
COPY ui/web ./ui/web
RUN cd ui/web && npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps + Node.js + the provider CLIs the agent runner drives.
#
# Both CLIs are baked into the image on purpose. Installing one with
# `docker exec plutus-mcp npm install -g …` writes to the container's *writable
# layer*, which `docker compose up -d` discards when it recreates the container
# from a pulled image — so a hand-installed Codex silently disappeared on every
# update and the card went back to "CLI not installed".
#
# Gemini is not here: it is an HTTP provider now (core/ai_providers), driven by a
# free-tier API key rather than a CLI login, so shipping @google/gemini-cli would
# add weight to every pull for a binary nothing invokes.
#
# bubblewrap is Codex's sandbox prerequisite; without it Codex warns and falls
# back to a bundled copy on every launch.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git \
    openssh-client \
    sshpass \
    cifs-utils \
    bubblewrap \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code @openai/codex \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Comfy-Org's comfy-mcp + comfy-cli, served by Plutus as comfy_* tools.
#
# Its own venv, not the app's: comfy-mcp requires mcp 2.x while Plutus is on 1.x,
# and the two only ever talk over stdio, so they never have to share a Python.
# Tracking is switched off at build time — comfy-cli asks about it on first run,
# and a question on a container's stdin is a hang. WITH_COMFY_MCP=0 leaves it out
# (~150 MB: comfy-cli bundles ffmpeg and uv).
ARG WITH_COMFY_MCP=1
RUN if [ "$WITH_COMFY_MCP" = "1" ]; then \
      python -m venv /opt/comfy-mcp \
      && /opt/comfy-mcp/bin/pip install --no-cache-dir "comfy-mcp>=0.10" "comfy-cli>=1.14" \
      && /opt/comfy-mcp/bin/comfy --skip-prompt tracking disable; \
    fi
# A path that does not exist (WITH_COMFY_MCP=0) simply means "not installed".
ENV COMFY_MCP_COMMAND=/opt/comfy-mcp/bin/comfy-mcp \
    COMFY_BIN=/opt/comfy-mcp/bin/comfy

# Copy source (ui/static/dist is .dockerignored, so it isn't clobbered below).
COPY . .

# Copy the built web UI from the node stage.
COPY --from=webbuild /build/ui/static/dist /app/ui/static/dist

# Create config dir
RUN mkdir -p /app/config

EXPOSE 8765 8766

# Liveness probe — UI server-health endpoint, which also verifies the MCP
# process is alive (503 if not). Skips cleanly in MCP-only mode (UI_ENABLED=false).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD [ "$UI_ENABLED" = "false" ] || curl -f http://localhost:8766/server/health || exit 1

CMD ["python", "main.py"]
