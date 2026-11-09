FROM python:3.13-slim

# Kept the same as app/__init__.py's __version__ (a test checks).
LABEL org.opencontainers.image.title="dexSTASH" \
      org.opencontainers.image.description="A self-hosted Pokémon card collection app" \
      org.opencontainers.image.version="0.29.0" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/mags-ob/dexSTASH"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEXSTASH_DB=/srv/data/dexstash.db

WORKDIR /srv

# OpenCV (used by the photo reader) needs these system libraries; tzdata
# makes TZ in .env work (the nightly price update runs at local time).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin dexstash

COPY requirements.lock .
RUN pip install -r requirements.lock

COPY app ./app
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
# Files copied from a NAS or Windows share can arrive readable by their owner
# only; the app runs as its own user, so make the code readable by everyone.
RUN chmod -R u=rwX,go=rX /srv/app && chmod 755 /usr/local/bin/entrypoint.sh

VOLUME /srv/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# One worker only: the login lockout and the job box live in one process. Scan photos are read in the
# scanner container (docker-compose.yml runs this image with: python -m app.scan_worker).
# The app itself reads the proxy's forwarded headers (app/main.py: private addresses, or DEXSTASH_PROXY_IPS),
# so uvicorn doesn't.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
