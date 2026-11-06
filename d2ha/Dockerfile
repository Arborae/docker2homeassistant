FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG D2HA_VERSION=dev
ENV D2HA_VERSION=${D2HA_VERSION}

RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home d2ha

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN chmod 755 entrypoint.sh

EXPOSE 12021
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:12021/healthz', timeout=4)"

ENTRYPOINT ["/app/entrypoint.sh"]
# One worker: the update checker and the job queue live in this process.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--bind", "0.0.0.0:12021", "--timeout", "120", "app:create_app()"]
