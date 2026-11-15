FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# libatomic1 dibutuhkan node bawaan Playwright; di image slim tidak ada, jadi
# `playwright install` gagal (exit 127) sebelum sempat memasang dependensi browser.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Tidak memakai `USER appuser`: entrypoint yang menurunkan hak akses setelah memperbaiki /data
RUN useradd -m -u 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser /data /ms-playwright \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
