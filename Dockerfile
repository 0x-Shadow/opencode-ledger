# syntax=docker/dockerfile:1
FROM python:3.14-slim

# Zero runtime dependencies — install straight from source.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENCODE_LEDGER_HOST=0.0.0.0 \
    OPENCODE_LEDGER_PORT=8765 \
    OPENCODE_LEDGER_DB=/data/opencode/opencode.db \
    OPENCODE_LEDGER_STATE_FILE=/data/state/ledger_state.json

WORKDIR /app
COPY pyproject.toml README.md ./
COPY opencode_ledger ./opencode_ledger
RUN pip install --no-cache-dir . \
 && groupadd --gid 1000 ledger \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin ledger \
 && mkdir -p /data/opencode /data/state \
 && chown -R ledger:ledger /data

USER ledger
EXPOSE 8765

# Honors OPENCODE_LEDGER_PORT / OPENCODE_LEDGER_TOKEN when overridden.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python3", "-c", "import os,sys,urllib.request; p=os.environ.get('OPENCODE_LEDGER_PORT','8765'); req=urllib.request.Request('http://127.0.0.1:'+p+'/api/healthz'); t=os.environ.get('OPENCODE_LEDGER_TOKEN',''); req.add_header('X-Ledger-Token',t) if t else None; sys.exit(0 if urllib.request.urlopen(req, timeout=2).status==200 else 1)"]

CMD ["opencode-ledger", "serve"]
