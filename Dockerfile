FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY lp_core.py arb_core.py ind_core.py sso_core.py lp-web.py ./
COPY static/ ./static/

EXPOSE 8765

CMD ["python", "lp-web.py", "--host", "0.0.0.0", "--no-browser"]
