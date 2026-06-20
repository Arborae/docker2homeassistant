FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG D2HA_VERSION=dev
ENV D2HA_VERSION=${D2HA_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gosu \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash d2ha

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copia l'entrypoint e rendilo eseguibile
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 12021

# Use gunicorn as production WSGI server.
# --workers 1: single worker because the app uses background threads (overview refresher, MQTT).
# --timeout 120: long timeout for heavy operations like full_update (pull + recreate).
# --threads 10: use gthread worker to prevent slow clients from blocking the single worker process.
CMD ["gunicorn", "-w", "1", "--threads", "10", "-b", "0.0.0.0:12021", "--timeout", "120", "app:app"]
