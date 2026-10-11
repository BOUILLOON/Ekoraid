FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates \
    && node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)' \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
# Optional trusted CA bundle for development behind a TLS-inspecting proxy.
# BuildKit keeps the supplied bundle out of image layers; verification stays on.
RUN --mount=type=secret,id=build_ca,target=/tmp/playlist-build-ca.pem \
    if [ -f /tmp/playlist-build-ca.pem ]; then \
      python -m pip install --cert /tmp/playlist-build-ca.pem --no-cache-dir -r requirements.txt; \
    else \
      python -m pip install --no-cache-dir -r requirements.txt; \
    fi
RUN groupadd --gid 1000 playlist \
    && useradd --uid 1000 --gid playlist --no-create-home playlist \
    && chown playlist:playlist /app
COPY --chown=playlist:playlist playlist25 /app/playlist25
COPY --chown=playlist:playlist scripts /app/scripts
USER playlist
# Compose uses host networking on Linux; the admin app remains on loopback.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; from playlist25.config import load_settings; urllib.request.urlopen('http://127.0.0.1:'+str(load_settings().guest_port)+'/health',timeout=3)"]
CMD ["python", "-m", "playlist25", "start"]
