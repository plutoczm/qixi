FROM node:24.18.0-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.15-slim AS runtime
ARG XUNQI_APP_COMMIT=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XUNQI_DATA_MODE=offline \
    XUNQI_APP_COMMIT=${XUNQI_APP_COMMIT} \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-compile -r requirements.lock.txt
COPY backend/ ./backend/
COPY scripts/dev.py ./scripts/dev.py
COPY --from=frontend /build/dist/ ./frontend/dist/
COPY docker/entrypoint.sh /usr/local/bin/xunqi-entrypoint
RUN chmod 755 /usr/local/bin/xunqi-entrypoint \
    && useradd --create-home --uid 10001 xunqi \
    && mkdir -p /app/runtime \
    && chown -R xunqi:xunqi /app/runtime
USER xunqi
EXPOSE 8000
ENTRYPOINT ["xunqi-entrypoint"]
