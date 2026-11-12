FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости отдельным слоем — кэшируются, пока не меняется uv.lock
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Настройки (LUXMS_URL, LUXMS_COOKIE, ...) передаются через переменные окружения, .env в образ не попадает.
# Сервер общается по stdio, поэтому запускать с `docker run -i`.
ENTRYPOINT ["luxms-mcp"]
