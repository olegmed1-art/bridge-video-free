# Reproducible, CPU-only runtime for the Universal Video Analyzer.
# The image never carries Drive credentials, source media, results, or a model download.
FROM python@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

ARG UNIVERSAL_VIDEO_SOURCE_COMMIT
LABEL org.opencontainers.image.title="bridge-school-universal-video" \
      org.opencontainers.image.revision="${UNIVERSAL_VIDEO_SOURCE_COMMIT}" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12.14-slim-bookworm" \
      org.opencontainers.image.base.digest="sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg fonts-dejavu-core tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 universal-video \
 && useradd --uid 10001 --gid universal-video --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin universal-video

WORKDIR /opt/bridge-school/universal-video-src
COPY requirements-worker.txt ./
RUN pip install --no-cache-dir -r requirements-worker.txt
COPY universal_video ./universal_video
COPY bridge_neon_persistence.py bridge_output_scoped_idempotency.py \
     bridge_runtime_hardening_r25_1.py bridge_runtime_hardening_r25_2.py \
     bridge_runtime_hardening_r25_3.py bridge_runtime_hardening_r25_4.py \
     bridge_runtime_hardening_r25_6.py bridge_runtime_hardening_r25_7.py \
     bridge_runtime_hardening_r25_14.py bridge_runtime_hardening_r25_15.py \
     bridge_runtime_hardening_r25_16.py bridge_runtime_hardening_r5.py \
     bridge_report_board_reconstruction.py bridge_semantic_qc.py \
     bridge_speaker_diarization.py bridge_speaker_diarization_v2.py \
     bridge_speaker_diarization_v3.py bridge_speaker_diarization_v3_core.py \
     bridge_speaker_diarization_v3_repair.py bridge_speaker_identity_postprocess.py \
     bridge_speaker_mapping_r29.py bridge_worker_3_1_free.py \
     diana_longitudinal_quality_v2.py r29_identity_overlay_probe.py \
     route_drive_job_outputs.py run_drive_3_1_free.py run_drive_3_1_free_generic.py \
     run_drive_3_1_free_oidc.py run_master_3_1_free.py \
     run_master_3_1_free_semantic.py run_master_3_1_free_semantic_v2.py \
     transcript_stage_checkpoint_v1.py ./
COPY bridge_contracts ./bridge_contracts
COPY bridge_vision ./bridge_vision
COPY database ./database
COPY deploy/oracle-universal-video/universal-video-container-entrypoint.sh /usr/local/bin/universal-video-container-entrypoint
RUN chmod 0755 /usr/local/bin/universal-video-container-entrypoint \
 && install -d -o universal-video -g universal-video -m 0750 \
      /var/lib/universal-video/spool /var/lib/universal-video/output \
      /var/lib/universal-video/media /var/lib/universal-video/model-cache \
      /var/lib/universal-video/model-cache/speaker

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE=1 \
    UNIVERSAL_VIDEO_SPOOL_ROOT=/var/lib/universal-video/spool \
    UNIVERSAL_VIDEO_OUTPUT_ROOT=/var/lib/universal-video/output \
    UNIVERSAL_VIDEO_MEDIA_ROOT=/var/lib/universal-video/media \
    UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE=/var/lib/universal-video/model-cache/speaker \
    BRIDGE_SPEAKER_ALLOW_NEMO_COMPAT=0 \
    HF_HOME=/var/lib/universal-video/model-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

USER universal-video:universal-video
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/universal-video-container-entrypoint"]
CMD ["python", "-m", "universal_video.spool_worker"]
