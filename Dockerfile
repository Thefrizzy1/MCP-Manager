FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    sshpass \
    cifs-utils \
    && rm -rf /var/lib/apt/lists/*

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
