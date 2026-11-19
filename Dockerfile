# syntax=docker/dockerfile:1.7

FROM python:3.15-slim-trixie AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt \
 && /opt/venv/bin/pip uninstall -y pip

FROM python:3.15-slim-trixie
ARG VERSION=dev
LABEL org.opencontainers.image.title="trv-webcams" \
      org.opencontainers.image.description="Timelapses från Trafikverkets webbkameror" \
      org.opencontainers.image.source="https://github.com/tubalainen/trv-webcams" \
      org.opencontainers.image.version="${VERSION}"

# Debian's ffmpeg is built with NVENC/NVDEC (loaded at runtime from the NVIDIA driver),
# VAAPI and QSV support. Security updates come through apt.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends ffmpeg tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 # pip is not needed at runtime; removing it shrinks the attack surface
 && rm -rf /usr/local/lib/python3*/site-packages/pip* /usr/local/bin/pip* \
 && groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --create-home --home-dir /home/app app \
 && mkdir -p /data && chown app:app /data

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    PORT=8933 \
    TZ=Europe/Stockholm \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

# The entrypoint starts as root, fixes ownership of /data and then drops to
# uid/gid PUID/PGID (default 1000, the "app" user) before starting the app.
VOLUME ["/data"]
EXPOSE 8933

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8933\")}/api/health', timeout=4)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]
