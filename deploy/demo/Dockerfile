# Hosted demo image: the app, the worker and the replay connector, with locked dependencies. Built by compose.yml
# from the repository root through deploy/demo/demo.sh, which requires BuildKit. Dockerfile.dockerignore is an
# allowlist (only the code and synthetic data the stack runs); the legacy builder would ignore it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.11.18

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY . .
RUN useradd --system --uid 10001 --no-create-home demo
USER demo

ENTRYPOINT ["sh", "/app/deploy/demo/entrypoint.sh"]
