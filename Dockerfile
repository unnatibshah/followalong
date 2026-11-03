FROM python:3.12-slim

# ffmpeg converts uploads to MP4 and extracts end frames (tutorial processing)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY config ./config
COPY web ./web
COPY data ./data
COPY eval ./eval

RUN useradd --create-home app && mkdir -p /var/data && chown -R app /app/data /var/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/healthz')"
# Render sets PORT; locally it is 8000. No access log, so no IP addresses are written. --proxy-headers: the client
# IP (the per-IP live-session limit) comes from Render's proxy's X-Forwarded-For, not the proxy's own address.
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --proxy-headers --forwarded-allow-ips '*'"]
