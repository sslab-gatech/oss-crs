FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml .
COPY seed_watcher.py .
COPY seed_utils.py .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

RUN chmod +x seed_watcher.py

# Create directories for mounts
RUN mkdir -p /watch/corpus /watch/povs /ensemble/corpus /ensemble/povs

CMD ["python3", "seed_watcher.py"]
