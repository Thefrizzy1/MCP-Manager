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

# System deps + Node.js (for the headless agent's Claude Code CLI) + git.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git \
    openssh-client \
    sshpass \
    cifs-utils \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
