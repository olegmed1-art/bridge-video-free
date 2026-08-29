# Reproducible, CPU-only runtime for the Universal Video Analyzer.
#
# The image never carries Drive credentials, source media, results, or a model
# download. Oracle supplies those only through explicit protected mounts.
FROM python:3.12-slim-bookworm

ARG UNIVERSAL_VIDEO_SOURCE_COMMIT
LABEL org.opencontainers.image.title="bridge-school-universal-video" \
      org.opencontainers.image.revision="${UNIVERSAL_VIDEO_SOURCE_COMMIT}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 universal-video \
 && useradd --uid 10001 --gid universal-video --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin universal-video

WORKDIR /opt/bridge-school/universal-video-src
COPY requirements-universal-video.txt requirements-universal-video-speaker.txt ./
RUN pip install --no-cache-dir -r requirements-universal-video-speaker.txt
COPY universal_video ./universal_video
COPY deploy/oracle-universal-video/universal-video-container-entrypoint.sh /usr/local/bin/universal-video-container-entrypoint
RUN chmod 0755 /usr/local/bin/universal-video-container-entrypoint \
 && install -d -o universal-video -g universal-video -m 0750 \
      /var/lib/universal-video/spool \
      /var/lib/universal-video/output \
      /var/lib/universal-video/media \
      /var/lib/universal-video/model-cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE=1 \
    UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool \
    UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output \
    UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media \
    HF_HOME=/var/lib/universal-video/model-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

USER universal-video:universal-video
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/universal-video-container-entrypoint"]
CMD ["python", "-m", "universal_video.spool_worker"]
