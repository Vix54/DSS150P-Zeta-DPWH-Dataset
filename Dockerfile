FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY config ./config
COPY sql ./sql

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["validate-env"]
