FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.9.2

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN uv sync --frozen --no-dev
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && chown -R app:app /app

ENV PATH="/app/.venv/bin:${PATH}"
USER app

CMD ["python", "-m", "solution3.workers.reconciler"]
