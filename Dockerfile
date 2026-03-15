# ── Stage 1: build ────────────────────────────────────────────────────────────
# Install all dependencies into a venv. Build tools stay in this stage only.
FROM python:3.12-slim AS builder

# SPACY_MODEL: sm (~12 MB, fast) | md (~43 MB) | lg (~750 MB, most accurate)
ARG SPACY_MODEL=en_core_web_sm

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
        torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir gunicorn

# Download the spaCy NLP model used by Presidio.
# Baked into the image so the container starts without a network call.
RUN python -m spacy download ${SPACY_MODEL}

# Strip __pycache__ and *.dist-info test files to trim venv size
RUN find /venv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
 && find /venv -type d -name "tests" -path "*/site-packages/*" -exec rm -rf {} + 2>/dev/null || true


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# Re-declare ARG so it's visible in this stage, then bake it as an env var.
# pydantic-settings reads PII__SPACY_MODEL automatically, keeping the baked
# model and the runtime config in sync without any manual override needed.
ARG SPACY_MODEL=en_core_web_sm
ENV PII__SPACY_MODEL=${SPACY_MODEL}

# Non-root user — never run application code as root
RUN groupadd --gid 1001 app \
 && useradd --uid 1001 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# Copy the entire venv from builder (no compiler needed at runtime)
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy application code (respects .dockerignore)
COPY --chown=app:app . .

# Persistent data directories — override with volume mounts in production
RUN mkdir -p knowledge_base chroma_data \
 && chown -R app:app knowledge_base chroma_data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" \
        || exit 1

# Use multiple workers for production.
# Override WORKERS at runtime: docker run -e WORKERS=4 ...
# --preload loads the app (spaCy, PyTorch, ChromaDB) once in the master process;
# workers inherit memory via CoW fork instead of each loading models independently.
# Tell glibc to return freed memory to the OS more aggressively.
# Reduces RSS growth from allocator fragmentation (common with NumPy/PyTorch).
ENV MALLOC_TRIM_THRESHOLD_=100000
ENV WORKERS=1

CMD gunicorn app.main:app \
        --bind 0.0.0.0:8000 \
        --workers ${WORKERS} \
        --worker-class uvicorn.workers.UvicornWorker \
        --preload \
        --access-logfile -
