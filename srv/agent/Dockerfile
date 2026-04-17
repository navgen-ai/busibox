# =============================================================================
# Busibox Agent API Dockerfile
# =============================================================================
#
# AI Agent service for Busibox platform.
# Provides agent execution, tool calling, and workflow management.
#
# Build:
#   docker build -t busibox-agent .
#
# Run:
#   docker run -p 8000:8000 --env-file .env busibox-agent
#
# =============================================================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY agent/requirements.txt .

# Install system dependencies: ffmpeg for audio, libs needed by Playwright Chromium,
# plus libcurl/libxml2 used by curl_cffi and trafilatura/readability-lxml.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libxshmfence1 libx11-xcb1 \
    libcurl4 libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (includes curl_cffi, playwright-stealth, trafilatura, pypdf)
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binary (Tier 2 of the stealth scraper)
RUN python -m playwright install chromium

# Optional: install Camoufox (Tier 3, anti-detect Firefox) when ENABLE_CAMOUFOX=true
# This adds ~200MB and is only needed for sites blocking Playwright+stealth.
ARG ENABLE_CAMOUFOX=false
RUN if [ "$ENABLE_CAMOUFOX" = "true" ]; then \
      pip install --no-cache-dir "camoufox[geoip]>=0.4.0" && \
      python -m camoufox fetch; \
    fi

# Install aider-chat in an isolated venv to avoid dependency conflicts.
# The BuilderLocalAgent calls `aider` as a subprocess CLI tool.
# Placed before COPY shared/ so that frequent shared-library changes don't
# bust the cache for this expensive (~5 min) install.
RUN python -m venv /opt/aider && \
    /opt/aider/bin/pip install --no-cache-dir aider-chat>=0.78.0
# Keep system Python first for migrations/runtime; only use aider CLI when invoked.
ENV PATH="$PATH:/opt/aider/bin"

# Copy and install busibox_common shared library
COPY shared/ ./shared/
RUN pip install --no-cache-dir ./shared

# Copy source code
COPY agent/app/ ./app/
COPY agent/alembic/ ./alembic/
COPY agent/alembic.ini .

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Health check using Python (no curl needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application with auto-migrations
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
