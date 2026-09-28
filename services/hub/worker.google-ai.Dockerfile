# Compatibility image. Authenticated browser tools require a local desktop worker;
# they deliberately do not expose a host CDP port to containers.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/src
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY services/hub/pyproject.toml /app/
RUN uv pip install --system --no-cache-dir . && useradd --create-home orion
COPY services/hub/src /app/src
COPY services/shared /app/services/shared
USER orion
CMD ["python", "-m", "orion.worker.main"]
