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

# Copy source
COPY . .

# Create config dir
RUN mkdir -p /app/config

EXPOSE 8765 8766

# Liveness probe — UI server-health endpoint, which also verifies the MCP
# process is alive (503 if not). Skips cleanly in MCP-only mode (UI_ENABLED=false).
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD [ "$UI_ENABLED" = "false" ] || curl -f http://localhost:8766/server/health || exit 1

CMD ["python", "main.py"]
