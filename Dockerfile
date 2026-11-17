FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKERS=2

WORKDIR /app

# Dependencies first (cached layer). The spaCy model is pinned by URL + sha256 in
# requirements.txt, so it is baked into the image: no downloads at runtime.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pii_engine.py api.py ./

# Run as a non-root user
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# Each worker loads its own copy of the model (~1 GB RAM per worker).
# --no-access-log: access logs only hold paths, but keep logs minimal around PII.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --no-access-log"]
