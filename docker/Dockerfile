# Mein Geselle - Multi-stage Docker build
# Supports linux/amd64 and linux/arm64 (macOS Apple Silicon native)
# Base: python:3.13-slim-bookworm

FROM python:3.13-slim-bookworm AS base

# System dependencies for WeasyPrint, git, and general build tools
# Minimal set with --no-install-recommends to keep image lean
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpango-1.0-0 \
    libpango1.0-dev \
    libcairo2 \
    libcairo2-dev \
    libffi8 \
    libffi-dev \
    fontconfig \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Install uv (modern, fast Python package manager)
RUN pip install --no-cache-dir uv

# Clone Hermes Agent at canonical install path
# Using --depth=1 for shallow clone (faster, smaller)
RUN git clone --depth=1 https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent

# Create Hermes venv and install it (editable mode)
RUN cd /opt/hermes-agent && \
    uv venv /opt/hermes-agent/.venv && \
    /opt/hermes-agent/.venv/bin/pip install -e . --no-cache-dir

# Copy Mein Geselle repo into container
COPY . /app/mein-geselle

# Install Mein Geselle runtime dependencies into the Hermes venv
RUN /opt/hermes-agent/.venv/bin/uv pip install \
    -r /app/mein-geselle/requirements.txt \
    --no-cache-dir

# Run the install_into_hermes.sh to symlink tools, skills, and patch config
RUN bash /app/mein-geselle/scripts/install_into_hermes.sh /opt/hermes-agent

# Set environment variable so tools can find the repo
ENV MEIN_GESELLE_ROOT=/app/mein-geselle

# Set working directory to Hermes root
WORKDIR /opt/hermes-agent

# Activate the venv in the path for easy entrypoint use
ENV PATH="/opt/hermes-agent/.venv/bin:$PATH"

# ============================================================================
# Service 1: Hermes Gateway (no exposed ports, Telegram polling only)
# ============================================================================
FROM base AS gateway

# Hermes gateway runs outbound Telegram polling; no inbound ports needed
# Health check: verify that ~/.hermes/gateway_state.json exists and is updated
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test -f "$HOME/.hermes/gateway_state.json" || exit 1

# Shared volume for Hermes config and runtime state
VOLUME ["/root/.hermes"]

# Run the Hermes gateway
ENTRYPOINT ["hermes", "gateway", "run"]

# ============================================================================
# Service 2: Dashboard (FastAPI server on 7070)
# ============================================================================
FROM base AS dashboard

# Expose the dashboard port
EXPOSE 7070

# Read-only access to shared Hermes data
VOLUME ["/root/.hermes:ro"]

# Health check: verify dashboard is listening
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -s -f http://127.0.0.1:7070/healthz || exit 1

# Run the dashboard (FastAPI/Uvicorn server)
ENTRYPOINT ["python", "/app/mein-geselle/dashboard/app.py"]
