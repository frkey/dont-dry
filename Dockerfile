# Pinned by digest; Dependabot proposes updates
FROM python:3.13-slim@sha256:4873c73fa393882cf10f9d1054c928e95201c1559ca2107c1589786ece10f49f

# DejaVu draws the page's text; without it Pillow falls back to a tiny bitmap font
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 dontdry \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dontdry \
    && install -d -m 700 -o 10001 -g 10001 /var/lib/dont-dry

WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Exact versions checked against their hashes, then dont-dry itself without fetching anything
COPY deploy/requirements.txt deploy/requirements.txt
RUN pip install --require-hashes -r deploy/requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps --no-build-isolation . && dont-dry --version

USER 10001:10001
# The heartbeat and last-run.json. docker-compose.yml points STATE_DIR at a volume on
# /var/lib/dont-dry, so the last print survives a restart (dont-dry never prints again within an
# hour of one that reached the printer); /tmp keeps older read-only setups working.
ENV STATE_DIR=/tmp/dont-dry \
    TZ=UTC

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["dont-dry", "healthcheck"]

ENTRYPOINT ["dont-dry"]
CMD ["run"]
