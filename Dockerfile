FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Set non-sensitive environment variables. Secrets (API keys, JWT_SECRET_KEY) are passed
# when the container starts, never baked into the image.
ARG APP_ENV=production

ENV APP_ENV=${APP_ENV} \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv installs exactly the versions pinned in uv.lock
RUN pip install uv==0.13.8

# Copy the dependency files first to leverage Docker cache: this layer is rebuilt only when
# the dependencies change, not on every code change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application (.dockerignore keeps .env files, .git and caches out of the image)
COPY . .

# Make the entrypoint executable and create a non-root user that owns the app and its logs
RUN chmod +x /app/scripts/docker-entrypoint.sh \
    && useradd -m appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

# Default port
EXPOSE 8000

# Log the environment we're using
RUN echo "Using ${APP_ENV} environment"

# Command to run the application. uvicorn closes idle keep-alive connections after 5 seconds
# by default, the same moment many clients reuse them, so a request can be sent on a socket
# the server is closing ("server disconnected"). Keep connections longer than any client or
# load balancer does (AWS ALB idles out at 60 seconds).
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
