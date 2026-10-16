# API image with Python + Node + Playwright browsers (live E2E in-container)
# Keep PLAYWRIGHT_VERSION in sync with src/playwright_version.py
ARG PLAYWRIGHT_VERSION=1.61.1
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-jammy

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOSTS=

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
  PIP_INDEX_URL=${PIP_INDEX_URL} \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    APTP_PLAYWRIGHT_VERSION=${PLAYWRIGHT_VERSION} \
    APTP_SKIP_PLAYWRIGHT_INSTALL=true \
    APTP_MOCK_PLAYWRIGHT_EXECUTION=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    libpq-dev \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN set -eux; \
    PIP_TRUSTED_HOST_ARGS=""; \
    if [ -n "$PIP_TRUSTED_HOSTS" ]; then \
      for host in $PIP_TRUSTED_HOSTS; do \
        PIP_TRUSTED_HOST_ARGS="$PIP_TRUSTED_HOST_ARGS --trusted-host $host"; \
      done; \
    fi; \
    pip install --no-cache-dir --index-url "$PIP_INDEX_URL" $PIP_TRUSTED_HOST_ARGS -r requirements.txt

COPY alembic.ini .
COPY deployment/ deployment/
COPY mcp/ mcp/
COPY src/ src/

RUN chmod +x deployment/docker/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health')" || exit 1

ENTRYPOINT ["deployment/docker/entrypoint.sh"]
