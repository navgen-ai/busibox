# =============================================================================
# Busibox Config API Dockerfile
# =============================================================================
#
# Centralised configuration service for the Busibox platform.
# Provides tiered access: public, authenticated, app-scoped, admin.
#
# Build:
#   docker build -t busibox-config-api .
#
# Run:
#   docker run -p 8012:8012 --env-file .env busibox-config-api
#
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

COPY config/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
RUN pip install --no-cache-dir ./shared

COPY config/src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8012

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8012/health/live')" || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8012"]
