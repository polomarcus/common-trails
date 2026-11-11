FROM python:3.13-slim

WORKDIR /app

# System dependencies for psycopg2, shapely, tippecanoe (PMTiles build),
# Pillow (PNG og-card rendering in app/api/share.py), and DejaVu fonts
# so Pillow has a usable family without bundling a TTF in the repo.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libpq-dev \
    libgeos-dev \
    libsqlite3-dev \
    zlib1g-dev \
    libjpeg-dev \
    libfreetype-dev \
    fonts-dejavu-core \
    git \
    curl \
    ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build tippecanoe from source (Felt/tippecanoe v2.79+, used by build_pmtiles job)
RUN git clone --depth 1 --branch 2.79.0 https://github.com/felt/tippecanoe.git /tmp/tippecanoe \
    && cd /tmp/tippecanoe \
    && make -j4 \
    && make install \
    && cd / \
    && rm -rf /tmp/tippecanoe

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[dev]"

# Copy application code (includes data/ with GT routes and the DFCI cache)
COPY . .

# Data dir: point to the app's data directory (no separate COPY needed)
ENV DATA_DIR=/app/data

# No elevation (DEM) tiles are baked into the image any more: they only served
# the OSM import of the retired map-matching pipeline (~520 MB, 12–14 min of
# cold CI build). Elevation gain comes from the uploaded files themselves.

# Frontend static files (populated by CI deploy workflow; empty .gitkeep for local builds)
COPY static-frontend/ /app/static-frontend/

EXPOSE 8787

# Retry alembic up to 5 times — CI healthcheck race condition means the DB
# container may accept TCP before PostgreSQL is fully ready for queries.
# If ALL attempts fail the container EXITS (non-zero) instead of starting the
# API on an un-migrated schema: the old loop ended with a successful `sleep`,
# so uvicorn started anyway. On Cloud Run a revision that cannot start never
# takes traffic — the previous revision keeps serving and the deploy fails
# loudly. See backend/start-web.sh (already in the image via `COPY . .`).
CMD ["sh", "/app/start-web.sh"]
