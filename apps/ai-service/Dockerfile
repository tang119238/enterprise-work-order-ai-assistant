FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY apps/ai-service/pyproject.toml ./pyproject.toml
RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && mkdir -p /models \
    && chown app:app /models
COPY apps/ai-service/app ./app
COPY apps/ai-service/alembic.ini ./alembic.ini
COPY apps/ai-service/alembic ./alembic
COPY knowledge ./knowledge

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
