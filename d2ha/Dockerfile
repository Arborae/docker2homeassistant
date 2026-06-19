FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG D2HA_VERSION=dev
ENV D2HA_VERSION=${D2HA_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 12021

# Use gunicorn as production WSGI server.
# --workers 1: single worker because the app uses background threads (overview refresher, MQTT).
# --timeout 120: long timeout for heavy operations like full_update (pull + recreate).
# --access-logfile -: write access logs to stdout for docker log collection.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:12021", "--timeout", "120", "--access-logfile", "-", "app:app"]
