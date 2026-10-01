FROM python@sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a
# Immutable Debian snapshot; no candidate sources or user credentials in build context.
RUN rm -f /etc/apt/sources.list.d/debian.sources && printf '%s\n' \
    'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260930T000000Z bookworm main' \
    'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20260930T000000Z bookworm-security main' \
    > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=0 update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 acceptance && useradd --uid 1000 --gid 1000 --no-log-init --no-create-home acceptance
COPY requirements.txt /opt/acceptance-requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: --no-deps -r /opt/acceptance-requirements.txt \
    && python -m pip check \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && python -m pip freeze --all \
    && dpkg-query -W
