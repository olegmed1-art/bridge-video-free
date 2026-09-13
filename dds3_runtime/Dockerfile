FROM bridge-school-dds3 AS dds-core

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements-api.txt .
COPY requirements-dds3-vision.txt .
COPY requirements-dds3-runtime.txt .
RUN pip install --no-cache-dir -r requirements-api.txt -r requirements-dds3-vision.txt -r requirements-dds3-runtime.txt
COPY bridge_school_api ./bridge_school_api
COPY dds3_runtime ./dds3_runtime
COPY --from=dds-core /opt/bridge-school-dds3/dds_pbn_cli /usr/local/bin/dds_pbn_cli
COPY --from=dds-core /opt/bridge-school-dds3/dds_position_worker /usr/local/bin/dds_position_worker
COPY --from=dds-core /opt/bridge-school-dds3/libdds3.so /opt/bridge-school-dds3/libdds3.so
ENV DDS3_CLI=/usr/local/bin/dds_pbn_cli
ENV DDS3_POSITION_WORKER=/usr/local/bin/dds_position_worker
ENV LD_LIBRARY_PATH=/opt/bridge-school-dds3
ENV PORT=8080
EXPOSE 8080
CMD ["sh","-c","uvicorn dds3_runtime.app:app --host 0.0.0.0 --port ${PORT}"]
