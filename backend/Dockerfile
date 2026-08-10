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

# Copy application code (includes data/ with GT routes, DFCI cache, trail cache)
COPY . .

# Data dir: point to the app's data directory (no separate COPY needed)
ENV DATA_DIR=/app/data

# Bake SRTM/Copernicus HGT tiles into the image so the OSM import job
# resolves elevation locally (no Open-Meteo runtime dep). France
# coverage = 9 viewfinder sheets ≈ 450 MB. We also bake the two
# cross-border presets (Cataluna in NE Spain, Italia Nord-Ovest)
# because the corresponding OSM import jobs would otherwise emit NULL
# elevation/slope for any segment outside the France bbox (e.g.
# Cataluna south of lat 41°). +~70 MB image. The CLI is stdlib-only
# so it runs before the heavy Python deps complete. We tolerate
# partial failures (one sheet 404) — local_dem.py degrades gracefully
# to None for any tile missing on disk.
#
# DEM_DIR defaults to ${DATA_DIR}/dem inside local_dem.py.
ARG SKIP_DEM_BAKE=0
RUN if [ "$SKIP_DEM_BAKE" = "0" ]; then \
        echo "Baking HGT tiles for France + cross-border into $DATA_DIR/dem ..." && \
        python -m app.cli.download_dem --region france --dem-dir "$DATA_DIR/dem" && \
        python -m app.cli.download_dem --region cataluna --dem-dir "$DATA_DIR/dem" && \
        python -m app.cli.download_dem --region italia-nord-ovest --dem-dir "$DATA_DIR/dem" || \
        echo "WARNING: HGT bake had errors; image still usable, missing tiles return None"; \
    fi

# Frontend static files (populated by CI deploy workflow; empty .gitkeep for local builds)
COPY static-frontend/ /app/static-frontend/

EXPOSE 8787

# Retry alembic up to 5 times — CI healthcheck race condition means the DB
# container may accept TCP before PostgreSQL is fully ready for queries.
CMD ["sh", "-c", "for i in 1 2 3 4 5; do alembic upgrade head && break || echo \"Alembic attempt $i failed, retrying in 3s...\" && sleep 3; done && uvicorn app.main:app --host 0.0.0.0 --port 8787"]
