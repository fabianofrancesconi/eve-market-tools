FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests "psycopg[binary,pool]>=3.2"

COPY lp_core.py arb_core.py ind_core.py sso_core.py pg_store.py pg_migrations.py exploration.py lp-web.py ./
COPY static/ ./static/

# Cache dir — mounted as a Railway volume in production so the SDE + JSON caches
# survive restarts. Durable user state (settings/tokens) lives in Postgres.
RUN mkdir -p /app/.eve_scanner_cache

EXPOSE 8765

# Bind Railway's $PORT when present; fall back to 8765 for local/ghcr use.
CMD ["sh", "-c", "python lp-web.py --host 0.0.0.0 --port ${PORT:-8765} --no-browser"]
