# Stage 1: install dependencies
FROM python:3.13-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests and sync (no dev deps)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Stage 2: runtime image
FROM python:3.13-slim

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY app/ ./app/

# Create a non-root user (uid 1000)
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown appuser /data

USER appuser

EXPOSE 8000

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
