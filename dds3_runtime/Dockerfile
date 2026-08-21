FROM python:3.12-slim AS dds-build
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone https://github.com/dds-bridge/dds.git && cd dds && git checkout cdd13cf5b700788ac8c1391501b42445b3129b45 && mkdir build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release && cmake --build . -j2

FROM python:3.12-slim
WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY bridge_school_api ./bridge_school_api
COPY dds3_runtime ./dds3_runtime
COPY dds3_cli /src_cli
COPY --from=dds-build /src/dds /src/dds
RUN apt-get update && apt-get install -y --no-install-recommends g++ && g++ -std=c++17 -O3 -I/src/dds/include /src_cli/dds_pbn_cli.cpp -L/src/dds/build -ldds -Wl,-rpath,/src/dds/build -o /usr/local/bin/dds_pbn_cli && apt-get purge -y g++ && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV DDS3_CLI=/usr/local/bin/dds_pbn_cli
ENV PORT=8080
EXPOSE 8080
CMD ["sh","-c","uvicorn dds3_runtime.app:app --host 0.0.0.0 --port ${PORT}"]
