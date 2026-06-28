FROM python:3.10-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY api /app/api
COPY config /app/config
COPY common /app/common
COPY log /app/log
COPY scripts /app/scripts

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app

RUN python -m venv $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install --upgrade pip && \
    $VIRTUAL_ENV/bin/pip install --no-cache-dir -e /app[api]

CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
