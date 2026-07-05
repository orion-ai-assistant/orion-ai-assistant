FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

RUN pip install --no-cache-dir uv

# 1. Bağımlılıkları önceden kur (Katman Önbelleği)
COPY pyproject.toml /app/
RUN uv pip install --system --no-cache-dir .

# 2. Kodları kopyala
COPY src /app/src

CMD ["sh", "-c", "exec uvicorn orion.api.main:app --host 0.0.0.0 --port ${HUB_CONTAINER_PORT}"]
