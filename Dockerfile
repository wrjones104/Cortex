# Build the web client, then ship it inside the Python package. Two stages so
# the runtime image carries no node, no npm cache, and no build tools.

FROM node:24-alpine AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

# Ollama runs on the host (or in its own container). Cortex only ever talks to
# it over HTTP, and never exposes it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CORTEX_DATA_DIR=/data \
    CORTEX_OLLAMA_HOST=http://host.docker.internal:11434

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY --from=web /build/dist/ ./src/cortex/webui/

RUN pip install --no-cache-dir ".[api]"

# The vault lives on a volume, so the container stays disposable and your
# notes do not.
VOLUME ["/data"]
EXPOSE 8765

# 0.0.0.0 inside the container is the container's own network, not the host's.
# Publish the port deliberately, and prefer a tailnet address on the host.
CMD ["cortex", "serve", "--host", "0.0.0.0", "--port", "8765"]
