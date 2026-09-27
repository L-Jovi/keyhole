# Evaluation only: synthetic files, no host mounts, keys or tunnel client.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock LICENSE ./
COPY docs/package.md ./docs/package.md
COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 keyhole
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY contrib/glama/bootstrap.py /app/bootstrap.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER keyhole
ENTRYPOINT ["/opt/venv/bin/python", "-I", "/app/bootstrap.py"]
