# API image (S0). Base digest is pinned after an actual tested build, see docs/BETRIEB.md.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/packages/domain:/app/packages/contracts:/app/packages/infrastructure:/app/services/llm_gateway:/app/workers/research:/app/apps/api
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps ./apps
COPY packages ./packages
COPY services ./services
COPY workers ./workers
COPY migrations ./migrations
COPY alembic.ini ./
COPY config ./config
EXPOSE 8000
CMD ["uvicorn", "campus_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
