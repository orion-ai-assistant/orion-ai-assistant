# Optional worker image with Google AI Mode browser support.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/src PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY services/hub/pyproject.toml /app/
RUN uv pip install --system --no-cache-dir '.[google-ai]' \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home orion
COPY services/hub/src /app/src
COPY services/shared /app/services/shared
USER orion
CMD ["python", "-m", "orion.worker.main"]
