FROM --platform=linux/amd64 python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./requirements-api.txt
COPY WHEEL_PINS.json verify_install.py /qualification-build/
RUN python -m pip --isolated install --disable-pip-version-check --no-cache-dir --retries 0 --timeout 10 --only-binary=:all: --require-hashes --index-url https://pypi.org/simple --report /qualification-build/install.json -r requirements-api.txt \
    && python -I -B /qualification-build/verify_install.py /qualification-build/WHEEL_PINS.json /qualification-build/install.json

COPY bridge_school_api ./bridge_school_api
COPY bridge_contracts ./bridge_contracts
COPY database/video_staging_read.py database/video_staging_lineage.py database/video_candidate_persistence.py database/video_result_persistence.py database/source_identity_persistence.py database/runtime_worker_preflight.py ./database/

EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "bridge_school_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
