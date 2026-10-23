FROM python:3.12-slim

WORKDIR /app

COPY requirements-runtime.txt ./
# Use a patched installer at build time, but omit its vendored libraries at runtime.
RUN pip install --no-cache-dir --upgrade pip==26.2.2 \
    && pip install --no-cache-dir -r requirements-runtime.txt \
    && python -m pip uninstall --yes pip

COPY lp_core.py arb_core.py ind_core.py ind_track.py ind_research.py sso_core.py pg_store.py pg_migrations.py exploration.py lp-web.py ./
COPY static/ ./static/

# Cache dir — mounted as a Railway volume in production so the SDE + JSON caches
# survive restarts. Durable user state (settings/tokens) lives in Postgres.
RUN mkdir -p /app/.eve_scanner_cache

EXPOSE 8765

# Bind Railway's $PORT when present; fall back to 8765 for local/ghcr use.
CMD ["sh", "-c", "python lp-web.py --host 0.0.0.0 --port ${PORT:-8765} --no-browser"]
