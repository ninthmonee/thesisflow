FROM node:24-slim AS frontend
WORKDIR /ui
COPY frontend/ .
RUN npm ci --ignore-scripts \
 && npm run vendor:patch \
 && npm run build

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY backend/ .
COPY --from=frontend /ui/dist ./tradingagents/web/ui
COPY --from=frontend /ui/legacy ./tradingagents/web/static
RUN pip install --no-cache-dir -c requirements.lock ".[web]"

FROM python:3.13-slim

# TRADINGAGENTS_IN_DOCKER lets an update notice name the rebuild command.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADINGAGENTS_IN_DOCKER=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

ENTRYPOINT ["tradingagents"]
