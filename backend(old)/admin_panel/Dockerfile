FROM python:3.11-slim

WORKDIR /app

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app

RUN python -m venv $VIRTUAL_ENV

COPY pyproject.toml /app/pyproject.toml
COPY admin_panel /app/admin_panel
COPY config /app/config
COPY common /app/common
COPY log /app/log

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e /app[admin_panel]

CMD ["uvicorn", "admin_panel.app.main:app", "--host", "0.0.0.0", "--port", "3000"]
