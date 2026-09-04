FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# The non-headless OpenCV wheel needs these libraries even when the runtime
# does not enable its local debug windows.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/calibrations /app/config /app/diagnostics /app/state

CMD ["vision-localizer", "--config", "/app/config/config.local.json", "--calibrations", "/app/calibrations", "--cache", "/app/state/last_good_config.json"]
