FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src/ src/
COPY migrations/ migrations/

RUN uv pip install --system --no-cache -e ".[dev]"

EXPOSE 8000
CMD ["uvicorn", "solution5.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
