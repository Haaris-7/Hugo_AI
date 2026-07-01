FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY alembic.ini ./
COPY alembic ./alembic

FROM base AS prod
RUN pip install --no-cache-dir .
ENV PYTHONPATH=/app/backend
CMD ["uvicorn", "hugo.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS dev
COPY hermes-plugin ./hermes-plugin
RUN pip install --no-cache-dir ".[dev]"
ENV PYTHONPATH=/app/backend
CMD ["uvicorn", "hugo.main:app", "--host", "0.0.0.0", "--port", "8000"]
