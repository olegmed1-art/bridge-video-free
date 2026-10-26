FROM --platform=linux/amd64 python@sha256:e8a38829209aea82774882e5157e1f7c3e8c8393ff53a697b3580896e57f345c
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /qualification
COPY requirements.lock WHEEL_PINS.json verify_wheels.py ./
RUN python -m pip --isolated install --disable-pip-version-check --no-cache-dir --no-deps --retries 0 --timeout 10 --only-binary=:all: --require-hashes --index-url https://pypi.org/simple --report /qualification/install.json -r requirements.lock \
 && python -m pip --isolated check \
 && python -I -B /qualification/verify_wheels.py /qualification/WHEEL_PINS.json /qualification/install.json \
 && python -I -B -c "import sys,psycopg,psycopg.pq; assert sys.version_info[:3]==(3,12,15); assert psycopg.__version__=='3.3.4'; assert psycopg.pq.__impl__=='binary'"
COPY source ./source
COPY sql ./sql
COPY native_fixture.py native_cases.py ./
USER 999:999
ENTRYPOINT ["python","-I","-B","/qualification/native_cases.py"]
