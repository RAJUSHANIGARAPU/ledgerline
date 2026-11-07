# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv==0.13.5
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core postgresql-client curl \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 ledgerline
WORKDIR /app
COPY --from=builder --chown=ledgerline:ledgerline /app /app
COPY --chown=ledgerline:ledgerline alembic.ini ./
COPY --chown=ledgerline:ledgerline migrations ./migrations
COPY --chown=ledgerline:ledgerline scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER ledgerline
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "ledgerline.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
